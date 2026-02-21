from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg
from beanie.odm.operators.find.comparison import In
from stfu_tg import Doc, KeyValue, Section, Title, VList

from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.db.models.chat import ChatType
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.args.fed_id import FedIdArg
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("List all chats in a federation"))
class FederationChatsHandler(SophieMessageHandler):
    """Handler for listing all chats in a federation."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("fchats",)), FeatureFlagFilter("new_feds_fchats")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {"fed_id": OptionalArg(FedIdArg(l_("Federation ID to list chats for (optional)")))}

    async def handle(self) -> Any:
        federation: Federation | None = self.data.get("fed_id")

        if federation:
            await self._show_federation_chats(federation)
            return

        if self.connection.type == ChatType.private:
            await self._show_user_federations()
            return

        await self._show_chat_federation()

    async def _show_federation_chats(self, federation: Federation) -> None:
        """List all chats in the federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        user_tid = self.event.from_user.id

        # Check permissions - need ban permission to view chat list
        if not await FederationPermissionService.can_ban_in_federation(federation, user_tid):
            await self.event.reply(_("You don't have permission to view this federation's chats."))
            return

        # Get chat count
        chat_count = len(federation.chats) if federation.chats else 0

        if chat_count == 0:
            doc = Doc(
                Title(_("🏛 Federation Chats")),
                KeyValue(_("Federation"), federation.fed_name),
                _("This federation has no chats."),
            )
            await self.event.reply(str(doc))
            return

        # Fetch chat models
        chat_iids = [chat.to_ref().id for chat in federation.chats]
        chats = await ChatModel.find(In(ChatModel.iid, chat_iids)).to_list()

        # Build chat list with details
        chat_items = []
        for chat in sorted(chats, key=lambda c: c.first_name_or_title or ""):
            chat_type_icon = "👥" if chat.type == ChatType.group else "📢" if chat.type == ChatType.channel else "💬"
            chat_items.append(KeyValue(f"{chat_type_icon} {chat.first_name_or_title}", chat.tid))

        # Create the document
        doc = Doc(
            Title(_("🏛 Federation Chats")),
            KeyValue(_("Federation"), federation.fed_name),
            KeyValue(_("Total chats"), str(chat_count)),
            "",
            Section(_("Chats in this federation"), VList(*chat_items) if chat_items else _("No chats found.")),
        )

        await self.event.reply(str(doc))

    async def _show_user_federations(self) -> None:
        """Show chats for user's federation(s) in PM."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        user_iid = self.data["user_db"].id
        federations = await FederationManageService.get_federations_by_creator(user_iid)

        if not federations:
            await self.event.reply(_("You don't own any federations."))
            return

        if len(federations) == 1:
            await self._show_federation_chats(federations[0])
            return

        federation_list = VList(*(KeyValue(fed.fed_name, fed.fed_id) for fed in federations))
        doc = Doc(
            Title(_("🏛 Your Federations")),
            _("You own multiple federations. Please specify which one to list chats for:"),
            federation_list,
            "",
            _("Usage:"),
            _("/fchats <federation_id>"),
        )
        await self.event.reply(str(doc))

    async def _show_chat_federation(self) -> None:
        """Show chats for the current chat's federation."""
        chat = await ChatModel.get_by_tid(self.connection.tid)
        if not chat:
            await self.event.reply(_("Chat not found in database"))
            return
        chat_iid = chat.iid
        federation = await FederationManageService.get_federation_for_chat(chat_iid)

        if not federation:
            await self.event.reply(_("This chat is not in any federation."))
            return

        await self._show_federation_chats(federation)
