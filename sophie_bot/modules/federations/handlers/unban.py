from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg
from babel.dates import format_date
from stfu_tg import Code, Doc, KeyValue, Template, Title, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.cache.locale import get_chat_locale
from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.db.models.federations import FederationTask
from sophie_bot.db.models.federations_enums import FederationTaskType
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services import FederationBanService, FederationManageService
from sophie_bot.modules.federations.services.common import normalize_chat_iids
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.modules.federations.utils.ban_docs import build_unban_reply_doc
from sophie_bot.modules.restrictions.utils.restrictions import unban_user as restrict_unban_user
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Unban a user from the federation"))
class FederationUnbanHandler(FederationCommandHandler):
    """Handler for unbanning users from federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("unfban", "funban")),)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        """Define arguments for the unban command."""
        base_args = await super().handler_args(message, data)
        base_args.update(
            {
                "user": OptionalArg(SophieUserArg(l_("User"))),
            }
        )
        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Unban user from federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        user: ChatModel | None = self.data.get("user")

        if not user:
            reply_message = self.event.reply_to_message
            reply_from_user = reply_message.from_user if reply_message else None
            if not reply_from_user:
                await self.event.reply(_("Please specify a user or reply to a user's message."))
                return
            user = await ChatModel.get_by_tid(reply_from_user.id)
            if not user:
                user = await ChatModel.upsert_user(reply_from_user)

        # Check permissions
        if not await self._check_permissions(federation):
            return

        # Check if user is banned
        ban = await FederationBanService.is_user_banned(federation.fed_id, user.tid)
        if not ban:
            await self._reply_user_not_banned()
            return

        # Attempt unban (removes the DB record - the FedBan middleware stops blocking immediately)
        was_unbanned, subscription_ban = await FederationBanService.unban_user(federation.fed_id, user.tid)
        if not was_unbanned:
            if subscription_ban and subscription_ban.origin_fed:
                await self._handle_subscription_ban_error(subscription_ban, user)
            else:
                await self.event.reply(_("Failed to unban user."))
            return

        # Snapshot the chats the user was actually banned in before the record is gone;
        # the scheduler clears the user from each of them.
        unban_chat_iids = normalize_chat_iids([chat.to_ref() for chat in ban.banned_chats]) if ban.banned_chats else []

        from_user = self.event.from_user
        banner = await ChatModel.get_by_tid(from_user.id)
        if not banner:
            await self.event.reply(_("Could not resolve the command user. Please try again."))
            return

        # Is current chat part of the federation?
        federation_chat_iids = (
            normalize_chat_iids([chat.to_ref() for chat in federation.chats]) if federation.chats else []
        )
        current_chat = self.connection.db_model
        chat_part_of_federation: bool = current_chat.iid in federation_chat_iids

        # Unban in the current chat right away; the scheduler propagates to the rest.
        immediate_chat_unbanned = False
        if chat_part_of_federation:
            immediate_chat_unbanned = await restrict_unban_user(self.event.chat.id, user.tid)

        # Immediate (in-progress) response; the scheduler edits it with the final counts.
        doc = build_unban_reply_doc(
            federation,
            user,
            from_user.id,
            from_user.first_name,
            propagating=True,
            immediate_chat_unbanned=immediate_chat_unbanned,
        )
        reply_msg = await common_try(
            self.event.reply(doc.to_html()),
            reply_not_found=lambda: self.event.answer(doc.to_html()),
        )

        # Propagate the unban across the chats the user was banned in via the scheduler.
        await FederationTask(
            fed_id=federation.fed_id,
            task_type=FederationTaskType.UNBAN,
            target_user_id=user.tid,
            user=banner.iid,
            chat=current_chat.iid,
            current_chat_iid=current_chat.iid if chat_part_of_federation else None,
            reply_chat_id=self.event.chat.id,
            reply_message_id=reply_msg.message_id if reply_msg else None,
            unban_chat_iids=unban_chat_iids,
            created_at=datetime.now(UTC),
        ).insert()

    async def _check_permissions(self, federation: Federation) -> bool:
        """Check if user has permission to unban in this federation."""
        if not self.event.from_user:
            return False
        banner_tid = self.event.from_user.id
        if not await FederationPermissionService.can_ban_in_federation(federation, banner_tid):
            await self.event.reply(_("You don't have permission to unban users in this federation."))
            return False
        return True

    async def _reply_user_not_banned(self) -> None:
        """Reply when user is not banned."""
        await self.event.reply(_("This user is not banned in this federation."))

    async def _handle_subscription_ban_error(self, subscription_ban, user: ChatModel) -> None:
        """Handle the case where unbanning is blocked due to subscription."""
        origin_fed = await FederationManageService.get_federation_by_id(subscription_ban.origin_fed)
        if not origin_fed:
            await self.event.reply(_("Cannot unban this user because their ban originated from a subscription."))
            return

        # Format ban date
        locale_name = await get_chat_locale(self.connection.db_model.iid)
        ban_date = format_date(subscription_ban.time.date(), "short", locale=locale_name)

        # Get banner user info - by is now a Link
        banner_user = await subscription_ban.by.fetch()
        banner_tid = banner_user.tid if banner_user else 0
        banner_name = banner_user.first_name_or_title if banner_user else _("Unknown")

        # Create detailed error message
        doc = Doc(
            Title(_("🏛 Cannot Unban User")),
            _("This user cannot be unbanned because they are banned in a federation this federation subscribes to."),
            "",
            KeyValue(_("📅 Banned on"), ban_date),
            KeyValue(_("🏛 Federation"), f"{origin_fed.fed_name} ({origin_fed.fed_id})"),
            KeyValue(_("👤 Banned by"), UserLink(banner_tid, banner_name)),
        )

        if subscription_ban.reason:
            doc += KeyValue(_("📝 Reason"), subscription_ban.reason)

        doc += ""
        doc += Template(_("To unban this user, you need to unsubscribe from the parent federation first:"))
        doc += Template(_("`/funsub {fed_id}`"), fed_id=Code(origin_fed.fed_id)).to_html()

        await reply_or_answer(self.event, doc)
