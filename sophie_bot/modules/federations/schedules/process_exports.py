from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
from io import StringIO
from typing import Final

from aiogram.types import BufferedInputFile

from sophie_bot.db.models.federations import FederationBan, FederationExportTask
from sophie_bot.services.bot import bot
from sophie_bot.utils.feature_flags import FeatureType, is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log
from stfu_tg import Doc, KeyValue, Title
from sophie_bot.db.models.federations_enums import TaskStatus

# Constants
CSV_EXPORT_FEATURE_FLAG: Final[FeatureType] = "new_feds_fbanlist"
BATCH_SIZE: Final[int] = 100


class ProcessFederationExports:
    """Scheduler job to process federation ban list exports."""

    async def handle(self) -> None:
        """Process all pending export tasks."""
        if not await is_enabled(CSV_EXPORT_FEATURE_FLAG):
            return

        tasks = await FederationExportTask.find(FederationExportTask.status == TaskStatus.PENDING).to_list()

        for task in tasks:
            try:
                await self._process_task(task)
            except Exception as e:
                log.error("Error processing federation export task", task_id=str(task.id), error=str(e))
                await self._update_task_status(task, TaskStatus.FAILED, str(e))

    async def _process_task(self, task: FederationExportTask) -> None:
        """Process a single export task."""
        await self._update_task_status(task, TaskStatus.PROCESSING)

        try:
            csv_bytes, ban_count = await self._generate_banlist_csv(task.fed_id)
            task.ban_count = ban_count
            task.file_size_bytes = len(csv_bytes)

            filename = f"{task.fed_id}_bans.csv"
            document = BufferedInputFile(csv_bytes, filename=filename)
            chat = await task.chat.fetch()
            message = await bot.send_document(
                chat_id=chat.tid,
                document=document,
                caption=self._build_caption(task, ban_count),
            )

            if message.document:
                task.file_id = message.document.file_id
            await self._update_task_status(task, TaskStatus.COMPLETED)

        except Exception as e:
            log.error("Failed to complete export", task_id=str(task.id), error=str(e))
            await self._update_task_status(task, TaskStatus.FAILED, str(e))
            raise

    async def _generate_banlist_csv(self, fed_id: str) -> tuple[bytes, int]:
        """Generate CSV banlist with streaming and batching."""
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["user_id", "reason", "by", "time", "banned_chats"])

        ban_count = 0
        async for ban in FederationBan.find(FederationBan.fed_id == fed_id):
            # Fetch banned chats to get tids
            banned_chat_tids = []
            for chat_link in ban.banned_chats or []:
                chat = await chat_link.fetch()
                if chat:
                    banned_chat_tids.append(chat.tid)
            banned_chats_str = "|".join(str(cid) for cid in banned_chat_tids)

            # Fetch by user to get tid
            by_user = await ban.by.fetch()
            by_tid = by_user.tid if by_user else 0

            writer.writerow([ban.user_id, ban.reason or "", by_tid, ban.time.isoformat(), banned_chats_str])
            ban_count += 1

            if ban_count % BATCH_SIZE == 0:
                await asyncio.sleep(0)

        csv_bytes = output.getvalue().encode("utf-8")
        return csv_bytes, ban_count

    def _build_caption(self, task: FederationExportTask, ban_count: int) -> str:
        """Build caption for exported document."""
        doc = Doc(
            Title(_("🏛 Federation Ban List Export")),
            KeyValue(_("Federation"), task.fed_name),
            KeyValue(_("Total bans"), ban_count),
            KeyValue(_("Exported at"), task.completed_at or datetime.now(timezone.utc).isoformat()),
        )
        return doc.to_html()

    async def _update_task_status(
        self,
        task: FederationExportTask,
        status: TaskStatus,
        error_message: str | None = None,
    ) -> None:
        """Update task status with optional error message."""
        task.status = status
        if error_message:
            task.error_message = error_message

        if status == TaskStatus.PROCESSING:
            task.started_at = datetime.now(timezone.utc)
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.completed_at = datetime.now(timezone.utc)

        await task.save()
