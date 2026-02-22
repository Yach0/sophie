from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, Italic, Template, Title, UserLink

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.services import FederationChatService, FederationManageService
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Leave a federation"))
class LeaveFederationHandler(SophieMessageHandler):
    """Handler for leaving federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("leavefed", "fleave")),
            FeatureFlagFilter("new_feds_leavefed"),
            UserRestricting(user_owner=True),
        )

    async def handle(self) -> Any:
        """Leave the current chat from its federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        # Check if chat is in a federation
        chat_iid = self.connection.db_model.iid
        federation = await FederationManageService.get_federation_for_chat(
            chat_iid,
        )
        if not federation:
            await self.event.reply(_("This chat is not in any federation."))
            return

        # Remove chat from federation
        removed = await FederationChatService.remove_chat_from_federation(federation, chat_iid)
        if not removed:
            await self.event.reply(_("Unable to leave the federation right now. Please try again."))
            return

        # Format success message
        doc = Doc(
            Title(_("🏛 Chat Left Federation")),
            Template(
                _("Chat '{chat_title}' has left federation '{fed_name}'."),
                chat_title=Italic(self.connection.title),
                fed_name=(federation.fed_name),
            ),
            Template(_("Federation ID: {fed_id}"), fed_id=federation.fed_id),
        )

        await self.event.reply(str(doc))

        # Log the chat leaving
        log_text = Template(
            _("🏛 Chat '{chat_title}' has left federation by {user}."),
            chat_title=Italic(self.connection.title),
            user=UserLink(self.event.from_user.id, self.event.from_user.first_name),
        ).to_html()
        await FederationManageService.post_federation_log(federation, log_text, self.event.bot)
