from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Code, Doc, Template, Title

from sophie_bot.db.models.federations import Federation
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.args.fed_id import FedIdArg
from sophie_bot.modules.federations.services import FederationChatService, FederationManageService
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Join a chat to a federation"))
class JoinFederationHandler(SophieMessageHandler):
    """Handler for joining chats to federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("joinfed", "fjoin")),
            FeatureFlagFilter("new_feds_joinfed"),
            UserRestricting(user_owner=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"fed_id": FedIdArg(l_("Federation ID to join"))}

    async def handle(self) -> Any:
        """Join the current chat to a federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        fed_id: Federation = self.data["fed_id"]

        # Check if chat is already in a federation
        chat_iid = self.connection.db_model.iid
        existing_fed = await FederationManageService.get_federation_for_chat(
            chat_iid,
        )
        if existing_fed:
            if existing_fed.fed_id == fed_id.fed_id:
                await self.event.reply(_("This chat is already in the specified federation."))
                return
            else:
                await self.event.reply(_("This chat is already in another federation. Leave it first."))
                return

        # Add chat to federation
        joined = await FederationChatService.add_chat_to_federation(
            fed_id,
            chat_iid,
        )
        if not joined:
            await self.event.reply(_("Unable to join the federation right now. Please try again."))
            return

        # Format success message
        doc = Doc(
            Title(_("🏛 Chat Joined Federation")),
            Template(
                _("Chat '{chat_title}' has been added to federation '{fed_name}'."),
                chat_title=self.connection.title,
                fed_name=fed_id.fed_name,
            ),
            Template(_("Federation ID: {fed_id}"), fed_id=Code(fed_id.fed_id)),
        )

        await self.event.reply(str(doc))

        # Log the chat joining
        log_text = Template(
            _("🏛 Chat '{chat_title}' has been added to federation by {user}."),
            chat_title=self.connection.title,
            user=self.event.from_user.mention_html(),
        ).to_html()
        await FederationManageService.post_federation_log(fed_id, log_text, self.event.bot)
