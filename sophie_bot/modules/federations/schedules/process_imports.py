from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from typing import Final, TypedDict

from aiogram.exceptions import TelegramBadRequest
from beanie import PydanticObjectId

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation, FederationBan, FederationImportTask
from sophie_bot.services.bot import bot
from sophie_bot.utils.feature_flags import FeatureType, is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log
from stfu_tg import Doc, KeyValue, Title
from sophie_bot.db.models.federations_enums import TaskStatus

# Constants
CSV_IMPORT_FEATURE_FLAG: Final[FeatureType] = "new_feds_import"
MAX_REASON_LENGTH: Final[int] = 500
REQUIRED_CSV_HEADERS: Final[set[str]] = {"user_id", "reason", "by", "time"}
BATCH_SIZE: Final[int] = 100


class BanRow(TypedDict):
    user_id: str
    reason: str
    by: str
    time: str


class BanData(TypedDict):
    fed_id: str
    user_id: int
    time: datetime
    by: PydanticObjectId
    reason: str | None


class CSVValidationError(ValueError):
    """Raised when CSV validation fails."""


class CSVDownloadError(ValueError):
    """Raised when CSV download fails."""


class BanValidationError(ValueError):
    """Raised when ban validation fails."""


class ProcessFederationImports:
    """Scheduler job to process federation ban list imports."""

    async def handle(self) -> None:
        """Process all pending import tasks."""
        if not await is_enabled(CSV_IMPORT_FEATURE_FLAG):
            return

        tasks = await FederationImportTask.find(FederationImportTask.status == TaskStatus.PENDING).to_list()

        for task in tasks:
            try:
                await self._process_task(task)
            except (csv.Error, CSVValidationError, CSVDownloadError, BanValidationError) as e:
                log.error("Error processing federation import task", task_id=str(task.id), error=str(e))
                await self._update_task_status(task, TaskStatus.FAILED, str(e))

    async def _process_task(self, task: FederationImportTask) -> None:
        """Process a single import task."""
        await self._update_task_status(task, TaskStatus.PROCESSING)

        try:
            federation = await Federation.find_one(Federation.fed_id == task.fed_id)
            if not federation:
                raise CSVValidationError("Federation not found")

            importer_user = await task.user.fetch()
            if not importer_user:
                raise CSVValidationError("Importing user not found")
            importer_user_tid = importer_user.tid

            reader = await self._download_and_parse_csv(task.file_id)

            imported_count = 0
            failed_count = 0
            pending_bans: list[FederationBan] = []

            existing_bans = await self._get_existing_bans(federation.fed_id)

            for row_num, row in enumerate(reader, start=2):
                try:
                    ban_data = await self._validate_and_parse_ban(federation, row, importer_user_tid, task.id)

                    user_id = ban_data["user_id"]
                    existing_ban = existing_bans.get(user_id)

                    if existing_ban:
                        await self._update_existing_ban(existing_ban, ban_data["reason"])
                        imported_count += 1
                    else:
                        ban = self._create_ban_entry(ban_data, task.id)
                        pending_bans.append(ban)
                        imported_count += 1

                        if len(pending_bans) >= BATCH_SIZE:
                            await FederationBan.insert_many(pending_bans)
                            pending_bans.clear()

                except BanValidationError as e:
                    failed_count += 1
                    log.warning("Failed to import ban row", task_id=str(task.id), row=row_num, error=str(e))

            # Insert remaining pending bans
            if pending_bans:
                await FederationBan.insert_many(pending_bans)

            await self._update_task_status(
                task, TaskStatus.COMPLETED, imported_count=imported_count, failed_count=failed_count
            )
            await self._send_completion_notification(task, federation)

        except (csv.Error, CSVValidationError, CSVDownloadError, BanValidationError) as e:
            await self._update_task_status(task, TaskStatus.FAILED, str(e))
            raise

    async def _download_and_parse_csv(self, file_id: str) -> csv.DictReader:
        """Download CSV file and parse it into a DictReader."""
        file = await bot.get_file(file_id)
        if not file.file_path:
            raise CSVDownloadError("Failed to get file path from Telegram")

        downloaded_bytes = await bot.download_file(file.file_path)
        if not downloaded_bytes:
            raise CSVDownloadError("Failed to download file from Telegram")

        file_bytes = downloaded_bytes.read()
        if not file_bytes:
            raise CSVDownloadError("Downloaded file is empty")

        file_text = file_bytes.decode("utf-8")
        reader = csv.DictReader(StringIO(file_text))

        if not REQUIRED_CSV_HEADERS.issubset(reader.fieldnames or []):
            raise CSVValidationError(
                f"Invalid CSV format. Required headers: {', '.join(REQUIRED_CSV_HEADERS)}, "
                f"got: {', '.join(reader.fieldnames or [])}"
            )

        return reader

    async def _get_existing_bans(self, fed_id: str) -> dict[int, FederationBan]:
        """Fetch existing bans for federation to avoid duplicates."""
        bans = await FederationBan.find(FederationBan.fed_id == fed_id).to_list()
        return {ban.user_id: ban for ban in bans}

    def _create_ban_entry(self, ban_data: BanData, task_id: object) -> FederationBan:
        """Create a new FederationBan entry from parsed data."""
        return FederationBan(
            fed_id=ban_data["fed_id"],
            user_id=ban_data["user_id"],
            time=ban_data["time"],
            by=ban_data["by"],
            reason=ban_data["reason"],
            fimport_id=task_id,
        )

    async def _update_existing_ban(self, existing_ban: FederationBan, new_reason: str | None) -> None:
        """Update existing ban reason if different."""
        if existing_ban.reason != new_reason:
            existing_ban.reason = new_reason
            await existing_ban.save()

    async def _validate_and_parse_ban(
        self, federation: Federation, row: dict[str, str], importer_user_tid: int, task_id: object
    ) -> BanData:
        """Validate and parse a single ban row."""
        user_id = self._validate_user_id(row.get("user_id", "").strip())
        reason = self._validate_reason(row.get("reason", "").strip())
        by_user_tid = self._validate_by_field(row.get("by", "").strip())
        ban_time = self._parse_ban_time(row.get("time", "").strip())

        await self._check_ban_permissions(user_id, federation, importer_user_tid)

        # Look up the by user to get their internal ID
        by_user = await ChatModel.get_by_tid(by_user_tid)
        if not by_user:
            raise BanValidationError(f"User {by_user_tid} not found in database")

        return BanData(
            fed_id=federation.fed_id,
            user_id=user_id,
            time=ban_time,
            by=by_user.iid,
            reason=reason,
        )

    def _validate_user_id(self, user_id_str: str) -> int:
        """Validate and parse user_id from CSV row."""
        if not user_id_str:
            raise BanValidationError("user_id is required")

        try:
            user_id = int(user_id_str)
        except ValueError:
            raise BanValidationError(f"Invalid user_id: {user_id_str}")

        if user_id <= 0:
            raise BanValidationError(f"Invalid user_id (must be positive): {user_id}")

        return user_id

    def _validate_by_field(self, by_str: str) -> int:
        """Validate and parse 'by' field from CSV row."""
        if not by_str:
            raise BanValidationError("'by' field is required")

        try:
            by_user_id = int(by_str)
        except ValueError:
            raise BanValidationError(f"Invalid 'by' field: {by_str}")

        if by_user_id <= 0:
            raise BanValidationError(f"Invalid 'by' field (must be positive): {by_str}")

        return by_user_id

    def _validate_reason(self, reason: str) -> str | None:
        """Validate reason field."""
        if not reason:
            return None

        if len(reason) > MAX_REASON_LENGTH:
            raise BanValidationError(f"Reason too long (max {MAX_REASON_LENGTH} characters)")

        return reason

    def _parse_ban_time(self, time_str: str) -> datetime:
        """Parse ban time from CSV row."""
        if not time_str:
            return datetime.now(timezone.utc)

        try:
            timestamp = float(time_str)
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, OSError):
            pass

        try:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except ValueError:
            raise BanValidationError(f"Invalid time format: {time_str}")

    async def _check_ban_permissions(self, user_id: int, federation: Federation, importer_user_tid: int) -> None:
        """Check if the ban is permitted for the given user."""
        if user_id in CONFIG.operators:
            raise BanValidationError(f"Cannot ban bot operator: {user_id}")

        if user_id == CONFIG.bot_id:
            raise BanValidationError("Cannot ban the bot")

        creator = await federation.creator.fetch()
        if creator and user_id == creator.tid:
            raise BanValidationError("Cannot ban federation owner")

        if federation.admins:
            for admin_link in federation.admins:
                admin = await admin_link.fetch()
                if admin and user_id == admin.tid:
                    raise BanValidationError("Cannot ban federation admin")

        if user_id == importer_user_tid:
            raise BanValidationError("Cannot ban yourself")

    async def _update_task_status(
        self,
        task: FederationImportTask,
        status: TaskStatus,
        error_message: str | None = None,
        imported_count: int | None = None,
        failed_count: int | None = None,
    ) -> None:
        """Update task status with optional error message and counts."""
        task.status = status
        if error_message:
            task.error_message = error_message
        if imported_count is not None:
            task.imported_count = imported_count
        if failed_count is not None:
            task.failed_count = failed_count

        if status == TaskStatus.PROCESSING:
            task.started_at = datetime.now(timezone.utc)
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.completed_at = datetime.now(timezone.utc)

        await task.save()

    async def _send_completion_notification(self, task: FederationImportTask, federation: Federation) -> None:
        """Send completion notification to user who initiated the import."""
        try:
            status_text = _("✅ Import completed successfully")
            if task.failed_count > 0:
                status_text = _("⚠️ Import completed with errors")

            doc = Doc(
                Title(status_text),
                KeyValue(_("Federation"), federation.fed_name),
                KeyValue(_("Imported"), task.imported_count),
                KeyValue(_("Failed"), task.failed_count) if task.failed_count else None,
            )

            if task.error_message:
                doc += KeyValue(_("Error"), task.error_message)

            chat = await task.chat.fetch()
            await bot.send_message(chat.tid, doc.to_html())
        except TelegramBadRequest as e:
            log.error("Failed to send import completion notification", task_id=str(task.id), error=str(e))
