from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, KeyValue, Title

from sophie_bot.db.models import Federation
from sophie_bot.db.models.federations import FederationImportTask
from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.federations.handlers.base import FederationCommandHandler
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Import federation ban list from CSV file"))
class FederationImportHandler(FederationCommandHandler):
    """Handler for importing federation ban lists from CSV files."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("importfbans", "fimport")), FeatureFlagFilter("new_feds_import")

    async def handle_federation_command(self, federation: Federation) -> Any:
        """Import federation ban list from CSV file."""
        if not self.event.from_user:
            await self.event.reply(_("This command can only be used by users."))
            return

        # Get document from message or reply-to message
        document = self.event.document
        if not document and self.event.reply_to_message:
            document = self.event.reply_to_message.document

        if not document:
            await self.event.reply(
                _("Please upload a CSV file with the command or reply to a message containing a CSV file.")
            )
            return

        if not document.file_name or not document.file_name.lower().endswith(".csv"):
            await self.event.reply(_("Please upload a CSV file (ending with .csv)."))
            return

        user_iid = self.data["user_db"].iid

        # Permission check - federation admin or owner
        if not await FederationPermissionService.can_ban_in_federation(federation, self.event.from_user.id):
            await self.event.reply(_("You don't have permission to import ban lists to this federation."))
            return

        # Create import task
        import_task = FederationImportTask(
            fed_id=federation.fed_id,
            chat=self.connection.db_model.iid,
            user=user_iid,
            file_id=document.file_id,
            status=TaskStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        await import_task.insert()

        # Confirm import started
        await self.event.reply(
            Doc(
                Title(_("📥 Federation Ban List Import Started")),
                KeyValue(_("Federation"), federation.fed_name),
                KeyValue(_("Status"), _("Queued for processing")),
                _(
                    "This might take a while.",
                ),
            ).to_html()
        )
