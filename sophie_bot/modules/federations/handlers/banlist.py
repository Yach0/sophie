from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg
from stfu_tg import Doc, KeyValue, Title

from sophie_bot.constants import MAX_BANLIST_EXPORT_SIZE
from sophie_bot.db.models.federations import FederationExportTask
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.args.fed_id import FedIdArg
from sophie_bot.modules.federations.services.federation import FederationService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.modules.federations.exceptions import (
    FederationContextError,
    FederationNotFoundError,
)


@flags.help(description=l_("Show list of banned users in federation"))
@flags.disableable(name="fbanlist")
class FederationBanListHandler(SophieMessageHandler):
    """Handler for showing federation ban lists."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("fbanlist", "exportfbans", "fexport")),
            FeatureFlagFilter("new_feds_fbanlist"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, Any]:
        return {
            "fed_id": OptionalArg(
                FedIdArg(l_("Federation ID (optional, uses current chat's federation if not specified)"))
            ),
        }

    async def handle(self) -> Any:
        """Create export task for federation ban list."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        fed_id_arg: str | None = self.data.get("fed_id")

        connection = self.connection
        user_iid = self.data["user_db"].iid
        user_tid = self.event.from_user.id

        try:
            federation = await FederationService.get_federation(fed_id_arg, connection, user_tid)
        except (FederationNotFoundError, FederationContextError) as e:
            await self.event.reply(str(e))
            return

        if not await FederationPermissionService.can_ban_in_federation(federation, user_tid):
            await self.event.reply(_("You don't have permission to view ban lists in this federation."))
            return

        ban_count = await FederationService.get_federation_ban_count(federation.fed_id)

        if ban_count == 0:
            await self.event.reply(
                Doc(
                    Title(_("🏛 Federation Ban List")),
                    KeyValue(_("Federation"), federation.fed_name),
                    _("This federation has no banned users."),
                ).to_html()
            )
            return

        if ban_count > MAX_BANLIST_EXPORT_SIZE:
            await self.event.reply(
                Doc(
                    Title(_("❌ Export Too Large")),
                    KeyValue(_("Federation"), federation.fed_name),
                    KeyValue(_("Total bans"), ban_count),
                    KeyValue(_("Maximum export size"), MAX_BANLIST_EXPORT_SIZE),
                    _("This federation has too many bans to export. Please contact bot owner for assistance."),
                ).to_html()
            )
            return

        existing_task = await FederationExportTask.find_one(
            FederationExportTask.fed_id == federation.fed_id,
            FederationExportTask.user.id == user_iid,
            FederationExportTask.status == TaskStatus.PENDING,
        )

        if existing_task:
            await self.event.reply(
                Doc(
                    Title(_("⏳ Export Already Queued")),
                    KeyValue(_("Federation"), federation.fed_name),
                    _(
                        "You already have an export task queued for this federation. "
                        "Please wait for it to complete before creating another one."
                    ),
                ).to_html()
            )
            return

        export_task = FederationExportTask(
            fed_id=federation.fed_id,
            fed_name=federation.fed_name,
            chat=self.connection.db_model.iid,
            user=user_iid,
            status=TaskStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        await export_task.insert()

        await self.event.reply(
            Doc(
                Title(_("📤 Federation Ban List Export Started")),
                KeyValue(_("Federation"), federation.fed_name),
                KeyValue(_("Total bans"), ban_count),
                KeyValue(_("Status"), _("Queued for processing")),
                _("Your ban list will be sent to this chat shortly."),
            ).to_html()
        )
