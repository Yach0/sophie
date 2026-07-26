from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg, TextArg

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.communities import CommunityTask
from sophie_bot.db.models.communities_enums import CommunityTaskType
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.communities.exceptions import CommunityBanValidationError
from sophie_bot.modules.communities.services import CommunityBanService, CommunityManageService
from sophie_bot.modules.communities.utils.ban_docs import build_ban_reply_doc
from sophie_bot.modules.federations.services.common import normalize_chat_iids
from sophie_bot.modules.restrictions.utils.logging import extract_offending_message_text
from sophie_bot.modules.restrictions.utils.restrictions import ban_user as restrict_ban_user
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.delayed_delete import schedule_message_deletion
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Ban a user from the whole community."))
class CommunityBanHandler(SophieMessageHandler):
    """Ban a user across every chat of the current chat's community."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("cban", "scban")),
            FeatureFlagFilter("communities"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {
            "user": OptionalArg(SophieUserArg(l_("User"), allow_unknown_id=True)),
            "reason": OptionalArg(TextArg(l_("?Reason"))),
        }

    async def handle(self) -> Any:
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        current_chat = self.connection.db_model
        community = await CommunityManageService.get_community_for_chat(current_chat)
        if not community:
            await self.event.reply(_("This chat is not part of a community."))
            return

        user: ChatModel | None = self.data.get("user")
        reason: str | None = self.data.get("reason")

        if not user:
            reply_message = self.event.reply_to_message
            reply_from_user = reply_message.from_user if reply_message else None
            if not reply_from_user:
                await self.event.reply(_("Please specify a user or reply to a user's message."))
                return
            user = await ChatModel.get_by_tid(reply_from_user.id) or ChatModel.get_user_model(reply_from_user)

        user_tid = user.tid
        banner = await ChatModel.get_by_tid(self.event.from_user.id)
        if not banner:
            await self.event.reply(_("Could not resolve the command user. Please try again."))
            return

        original_message_text = extract_offending_message_text(self.event.reply_to_message)

        # Generate an AI reason when replying to a message and none was provided.
        if not reason and self.event.reply_to_message:
            ai_reason = await generate_restriction_reason(
                current_chat,
                message_text=original_message_text,
                include_rules=True,
            )
            if ai_reason:
                reason = ai_reason

        # Record the ban (the CommunityBanMiddleware enforces it immediately; the scheduler
        # proactively bans the user across the rest of the community's chats).
        try:
            ban = await CommunityBanService.ban_user(
                community.community_tid, user_tid, banner.iid, reason, original_message_text
            )
        except CommunityBanValidationError as err:
            await self.event.reply(str(err))
            return

        # Ban in the current chat right away so a spamming user is stopped on the spot.
        immediate_chat_banned = await restrict_ban_user(self.event.chat.id, user_tid)
        if immediate_chat_banned:
            existing_chat_iids = set(normalize_chat_iids([chat.to_ref() for chat in ban.banned_chats]))
            if current_chat.iid not in existing_chat_iids:
                ban.banned_chats.append(current_chat)
                await ban.save()

        # Detect silent mode from the parsed command so it works with any prefix (/scban, !scban…).
        command_obj = self.data.get("command")
        silent = bool(command_obj and command_obj.command.lower() == "scban")

        doc = build_ban_reply_doc(
            community,
            user,
            self.event.from_user.id,
            self.event.from_user.first_name,
            reason,
            silent,
            propagating=True,
            immediate_chat_banned=immediate_chat_banned,
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

        # Propagate the ban across the rest of the community in the scheduler.
        await CommunityTask(
            community_tid=community.community_tid,
            task_type=CommunityTaskType.BAN,
            target_user_id=user_tid,
            user=banner.iid,
            chat=current_chat.iid,
            current_chat_iid=current_chat.iid,
            reply_chat_id=self.event.chat.id,
            reply_message_id=reply_msg.message_id if reply_msg else None,
            reason=reason,
            original_message_text=original_message_text,
            silent=silent,
            ban_id=ban.id,
            created_at=datetime.now(UTC),
        ).insert()
