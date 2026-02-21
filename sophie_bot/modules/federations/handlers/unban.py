from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg
from babel.dates import format_date
from stfu_tg import Doc, KeyValue, Title, UserLink, Template, Code

from sophie_bot.args.users import SophieUserArg
from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.db.models.language import LanguageModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services import FederationManageService, FederationBanService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Unban a user from the federation"))
class FederationUnbanHandler(FederationCommandHandler):
    """Handler for unbanning users from federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("unfban", "funban")), FeatureFlagFilter("new_feds_funban")

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

        # Attempt unban
        was_unbanned, subscription_ban = await FederationBanService.unban_user(federation.fed_id, user.tid)
        if not was_unbanned:
            if subscription_ban and subscription_ban.origin_fed:
                await self._handle_subscription_ban_error(subscription_ban, user)
            else:
                await self.event.reply(_("Failed to unban user."))
            return

        banned_chat_refs = [chat.to_ref() for chat in ban.banned_chats] if ban.banned_chats else []
        if banned_chat_refs:
            unbanned_count = await FederationBanService.unban_user_in_chat_iids(banned_chat_refs, user.tid)
        else:
            unbanned_count = 0

        # Success - format and send response
        await self._send_success_response(federation, user, unbanned_count)

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
        locale_name = await LanguageModel.get_locale(self.connection.db_model.iid)
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

        await self.event.reply(str(doc))

    async def _send_success_response(self, federation: Federation, user: ChatModel, unbanned_count: int) -> None:
        """Send success response for unbanning."""
        from_user = self.event.from_user
        if not from_user:
            return

        doc = Doc(
            Title(_("🏛 User Unbanned from Federation")),
            KeyValue(_("Federation"), federation.fed_name),
            KeyValue(_("User"), UserLink(user.tid, user.first_name_or_title or _("Unknown"))),
            KeyValue(_("Unbanned by"), UserLink(from_user.id, from_user.first_name)),
            KeyValue(_("Result"), Template(_("Unbanned in {count} chats"), count=str(unbanned_count))),
        )

        await self.event.reply(str(doc))

        # Log the unban
        log_text = Template(
            _("🏛 User {unbanned_user} has been unbanned from federation by {unbanner}."),
            unbanned_user=UserLink(user.tid, user.first_name_or_title or _("Unknown")),
            unbanner=from_user.mention_html(),
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)
