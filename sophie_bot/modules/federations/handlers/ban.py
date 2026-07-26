from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg, TextArg

from sophie_bot.args.users import SophieUserArg
from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.db.models.federations import FederationTask
from sophie_bot.db.models.federations_enums import FederationTaskType
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.federations.exceptions import FederationBanValidationError
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services import FederationBanService
from sophie_bot.modules.federations.services.common import normalize_chat_iids
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.modules.federations.utils.ban_docs import build_ban_reply_doc
from sophie_bot.modules.restrictions.utils.logging import extract_offending_message_text
from sophie_bot.modules.restrictions.utils.restrictions import ban_user as restrict_ban_user
from sophie_bot.modules.utils_.anonymous_admin import normalize_admin_title, resolve_anonymous_admin_candidates
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.delayed_delete import schedule_message_deletion
from sophie_bot.utils import flags
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Ban a user from the federation"))
class FederationBanHandler(FederationCommandHandler):
    """Handler for banning users from federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("fban", "sfban")),)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        """Define arguments for the fban command."""
        base_args = await super().handler_args(message, data)
        base_args.update(
            {
                "user": OptionalArg(SophieUserArg(l_("User"), allow_unknown_id=True)),
                "reason": OptionalArg(TextArg(l_("?Reason"))),
            }
        )
        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Ban user from federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        user: ChatModel | None = self.data.get("user")
        reason: str | None = self.data.get("reason")

        if not user:
            reply_message = self.event.reply_to_message
            reply_from_user = reply_message.from_user if reply_message else None
            if not reply_from_user:
                await self.event.reply(_("Please specify a user or reply to a user's message."))
                return
            user = await ChatModel.get_by_tid(reply_from_user.id)
            if not user:
                user = ChatModel.get_user_model(reply_from_user)

        user_tid = user.tid
        current_chat = self.connection.db_model

        resolved = await self._resolve_banner(current_chat)
        if resolved is None:
            return
        banner, banner_is_anonymous = resolved

        # Permission check
        if not await FederationPermissionService.can_ban_in_federation(federation, banner.tid):
            await self.event.reply(_("You don't have permission to ban users in this federation."))
            return

        original_message_text = extract_offending_message_text(self.event.reply_to_message)

        # Generate AI reason if none provided and replying to a message (federations don't include group rules)
        if not reason and self.event.reply_to_message:
            replied_text = original_message_text
            ai_reason = await generate_restriction_reason(
                self.connection.db_model,
                message_text=replied_text,
                include_rules=False,
            )
            if ai_reason:
                reason = ai_reason

        # Ban user (DB record - the FedBan middleware enforces the ban immediately,
        # before the scheduler proactively kicks the user from the federation's chats)
        try:
            ban = await FederationBanService.ban_user(federation, user_tid, banner.iid, reason, original_message_text)
        except FederationBanValidationError as err:
            await self.event.reply(str(err))
            return

        # Is current chat part of the federation?
        federation_chat_iids = (
            normalize_chat_iids([chat.to_ref() for chat in federation.chats]) if federation.chats else []
        )
        chat_part_of_federation: bool = current_chat.iid in federation_chat_iids

        # Ban in the current chat right away so a spamming user is stopped on the spot,
        # before the scheduler propagates the ban across the rest of the federation.
        immediate_chat_banned = False
        if chat_part_of_federation:
            immediate_chat_banned = await restrict_ban_user(self.event.chat.id, user_tid)
            if immediate_chat_banned:
                existing_chat_iids = set(normalize_chat_iids([chat.to_ref() for chat in ban.banned_chats]))
                if current_chat.iid not in existing_chat_iids:
                    ban.banned_chats.append(current_chat)
                    await ban.save()

        # Immediate (in-progress) response; the scheduler edits it with the final counts.
        # Detect silent mode from the parsed command name so it works with any command
        # prefix (e.g. /sfban, !sfban, .sfban) and an optional @mention.
        command_obj = self.data.get("command")
        silent = bool(command_obj and command_obj.command.lower() == "sfban")
        doc = build_ban_reply_doc(
            federation,
            user,
            self.event.from_user.id,
            self.event.from_user.first_name,
            reason,
            silent,
            propagating=True,
            immediate_chat_banned=immediate_chat_banned,
            banner_anonymous=banner_is_anonymous,
        )

        reply_msg = await common_try(
            self.event.reply(doc.to_html()),
            reply_not_found=lambda: self.event.answer(doc.to_html()),
        )

        if silent and reply_msg:
            messages_to_delete = [self.event.message_id, reply_msg.message_id]
            if self.event.reply_to_message:
                messages_to_delete.append(self.event.reply_to_message.message_id)
            schedule_message_deletion(self.event.chat.id, messages_to_delete)

        # Propagate the ban across the rest of the federation + subscriber chain in the scheduler.
        await FederationTask(
            fed_id=federation.fed_id,
            task_type=FederationTaskType.BAN,
            target_user_id=user_tid,
            user=banner.iid,
            chat=current_chat.iid,
            current_chat_iid=current_chat.iid if chat_part_of_federation else None,
            reply_chat_id=self.event.chat.id,
            reply_message_id=reply_msg.message_id if reply_msg else None,
            reason=reason,
            original_message_text=original_message_text,
            silent=silent,
            banner_anonymous=banner_is_anonymous,
            ban_id=ban.id,
            created_at=datetime.now(UTC),
        ).insert()

    async def _resolve_banner(self, current_chat: ChatModel) -> tuple[ChatModel, bool] | None:
        """Resolve the real user behind the command as the banner.

        Returns ``(banner, banner_is_anonymous)``, or ``None`` when the banner could not
        be resolved (a user-facing reply has already been sent in that case).

        When the ``fban_anonymous_admin`` flag is enabled and the command comes from an
        anonymous admin (Telegram's anonymous-admin bot as sender, with the group as
        ``sender_chat``), the real admin behind the custom author signature is resolved so
        the federation permission check and the fed log use their true identity.
        """
        from_user = self.event.from_user
        assert from_user is not None  # guarded by the caller

        is_anonymous_sender = (
            from_user.id == TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
            and self.event.sender_chat is not None
            and self.event.sender_chat.id == current_chat.tid
        )
        if is_anonymous_sender and await is_enabled("fban_anonymous_admin", chat_tid=current_chat.tid):
            return await self._resolve_anonymous_banner(current_chat)

        banner = await ChatModel.get_by_tid(from_user.id)
        if not banner:
            await self.event.reply(_("Could not resolve the command user. Please try again."))
            return None
        return banner, False

    async def _resolve_anonymous_banner(self, current_chat: ChatModel) -> tuple[ChatModel, bool] | None:
        title = normalize_admin_title(self.event.author_signature)
        if not title:
            await self.event.reply(_("Anonymous admin must have a custom admin title to use this command."))
            return None

        candidates = await resolve_anonymous_admin_candidates(current_chat.iid, title)
        if not candidates:
            await self.event.reply(
                _("Could not resolve this anonymous admin title. Refresh admin cache or use a unique title.")
            )
            return None
        if len(candidates) > 1:
            await self.event.reply(
                _("Multiple anonymous admins share this title. Use a unique title to run this command.")
            )
            return None

        banner = await candidates[0].user.fetch()
        if not isinstance(banner, ChatModel):
            await self.event.reply(_("Could not resolve the command user. Please try again."))
            return None
        return banner, True
