from __future__ import annotations

import json
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Code, Doc, Template, Title

from sophie_bot.db.models import Federation
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.args.fed_id import FedIdArg
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.services.redis import aredis
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Transfer federation ownership"))
class TransferOwnershipHandler(FederationCommandHandler):
    """Handler for transferring federation ownership."""

    TRANSFER_KEY_PREFIX = "fed_transfer:"
    TRANSFER_TTL = 300  # 5 minutes

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("transferfed", "ftransfer")),
            FeatureFlagFilter("new_feds_transferfed"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        """Define arguments for transfer command."""
        base_args = await super().handler_args(message, data)
        # Override fed_id to be required (not optional) for transfer
        base_args["fed_id"] = FedIdArg(l_("?Federation ID"))
        base_args["new_owner"] = TextArg(l_("New owner"))
        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Transfer federation ownership."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        new_owner_input: str = self.data["new_owner"]
        user_id = self.event.from_user.id

        # Check if user is the current owner
        creator = await federation.creator.fetch()
        if not creator or creator.tid != user_id:
            await self.event.reply(_("Only the federation owner can transfer ownership."))
            return

        # Parse new owner
        new_owner_id = await self._parse_user_id(
            new_owner_input,
        )
        if not new_owner_id:
            await self.event.reply(_("Invalid user. Please provide a valid username or user ID."))
            return

        # Cannot transfer to self
        if new_owner_id == user_id:
            await self.event.reply(_("You cannot transfer ownership to yourself."))
            return

        # Check if new owner is in the federation
        # TODO: Add validation to ensure new owner is eligible
        # (e.g., not bot operator, has not exceeded federation limit, etc.)

        # Create transfer request
        transfer_key = f"{self.TRANSFER_KEY_PREFIX}{federation.fed_id}"
        transfer_data = {
            "from_user": user_id,
            "to_user": new_owner_id,
            "fed_id": federation.fed_id,
            "fed_name": federation.fed_name,
        }

        # Store transfer request in Redis with TTL
        await aredis.set(
            transfer_key,
            json.dumps(transfer_data),
            ex=self.TRANSFER_TTL,
        )

        # Confirm to current owner
        confirm_doc = Doc(
            Title(_("🏛 Transfer Request Sent")),
            Template(
                _("Ownership transfer request sent to user {user_id}."),
                user_id=str(new_owner_id),
            ),
            Template(
                _("They have 5 minutes to accept with {cmd}"),
                cmd=Code(f"/accepttransfer {federation.fed_id}"),
            ),
        )

        await self.event.reply(str(confirm_doc))

    async def _parse_user_id(self, user_input: str) -> int | None:
        """Parse user ID from username or ID string.

        TODO: Implement proper user resolution for usernames via Telegram API.
        Currently only supports numeric user IDs.
        """
        try:
            return int(user_input)
        except ValueError:
            return None
