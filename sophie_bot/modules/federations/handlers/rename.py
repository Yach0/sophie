from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from stfu_tg import Doc, KeyValue, Title

from sophie_bot.constants import MAX_FEDERATION_NAME_LENGTH
from sophie_bot.db.models import Federation
from sophie_bot.db.models.federations import Federation as FederationModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Rename a federation (owner only)"))
class FederationRenameHandler(FederationCommandHandler):
    """Handler for renaming federations."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("frename",)), FeatureFlagFilter("new_feds_frename")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        """Define arguments for rename command."""
        base_args = await super().handler_args(message, data)
        base_args["new_name"] = TextArg(l_("New federation name"))
        return base_args

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Rename federation."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        new_name: str | None = self.data.get("new_name")
        if not new_name:
            await self.event.reply(_("Please provide a new federation name."))
            return

        if len(new_name) > MAX_FEDERATION_NAME_LENGTH:
            await self.event.reply(_("Federation name too long."))
            return

        if not await FederationPermissionService.validate_federation_owner(federation, self.event.from_user.id):
            await self.event.reply(_("Only federation owners can rename federations."))
            return

        existing = await FederationModel.find_one(FederationModel.fed_name == new_name)
        if existing and existing.fed_id != federation.fed_id:
            await self.event.reply(_("A federation with this name already exists."))
            return

        old_name = federation.fed_name
        federation.fed_name = new_name
        await federation.save()

        await self.event.reply(
            Doc(
                Title(_("🏛 Federation Renamed")),
                KeyValue(_("Old name"), old_name),
                KeyValue(_("New name"), new_name),
                KeyValue(_("Federation ID"), federation.fed_id),
            ).to_html()
        )
