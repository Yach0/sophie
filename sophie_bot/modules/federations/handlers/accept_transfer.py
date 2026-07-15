from __future__ import annotations

import json
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Code, Doc, Template, Title

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.services.redis import aredis
from sophie_bot.modules.utils_.acting_user import require_acting_user
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Accept federation ownership transfer"))
class AcceptTransferHandler(SophieMessageHandler):
    """Handler for accepting federation ownership transfers."""

    TRANSFER_KEY_PREFIX = "fed_transfer:"

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (CMDFilter(("accepttransfer",)),)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"fed_id": TextArg(l_("Federation ID to accept transfer for"))}

    async def handle(self) -> Any:
        """Accept federation ownership transfer."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        fed_id_input: str = self.data["fed_id"]
        user_db = await require_acting_user(self.event, self.data)
        if not user_db:
            return

        # The token stores the recipient's Telegram user ID, so compare against the acting user --
        # connection.tid is the chat ID, which only coincides with it in private chats.
        user_tid = user_db.tid

        transfer_key = f"{self.TRANSFER_KEY_PREFIX}{fed_id_input}"

        # Read the token first without deleting it, so an unauthorized caller cannot
        # consume (and destroy) a pending transfer intended for someone else.
        transfer_data_raw = await aredis.get(transfer_key)

        if not transfer_data_raw:
            await self.event.reply(_("No pending transfer request found for this federation."))
            return

        try:
            transfer_data = json.loads(transfer_data_raw)
        except json.JSONDecodeError:
            await self.event.reply(_("Invalid transfer request data."))
            return

        # Validate recipient before consuming the token
        if transfer_data.get("to_user") != user_tid:
            await self.event.reply(_("This transfer request is not for you."))
            return

        # Now atomically consume the token; if it was already claimed by a concurrent
        # request, getdel returns None and we bail out safely.
        if not await aredis.getdel(transfer_key):
            await self.event.reply(_("No pending transfer request found for this federation."))
            return

        if transfer_data.get("fed_id") != fed_id_input:
            await self.event.reply(_("Federation ID mismatch."))
            return

        # Get federation
        federation = await FederationManageService.get_federation_by_id(
            fed_id_input,
        )
        if not federation:
            await self.event.reply(_("Federation not found."))
            return

        # Verify current owner
        # We need current owner TID
        current_owner = await federation.creator.fetch()
        if not current_owner or current_owner.tid != transfer_data.get("from_user"):
            await self.event.reply(_("Transfer request is outdated. The federation owner has changed."))
            return

        # Transfer ownership
        await FederationManageService.update_federation(
            federation,
            {"creator": user_db},
        )

        # Format success message
        doc = Doc(
            Title(_("🏛 Ownership Transferred")),
            Template(_("You are now the owner of federation '{fed_name}'."), fed_name=federation.fed_name),
            Template(_("Federation ID: {fed_id}"), fed_id=Code(federation.fed_id)),
        )

        await self.event.reply(str(doc))
