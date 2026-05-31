from __future__ import annotations

from datetime import datetime, timedelta, timezone

from beanie.odm.operators.find.comparison import In

from sophie_bot.db.models.federations import FederationExportTask
from sophie_bot.utils.logger import log


class CleanupOldExports:
    """Scheduler job to clean up old completed/failed export tasks."""

    async def handle(self) -> None:
        """Clean up export tasks older than TTL."""
        from sophie_bot.constants import FEDERATION_EXPORT_TTL_DAYS

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=FEDERATION_EXPORT_TTL_DAYS)

        tasks_to_delete = await FederationExportTask.find(
            In(FederationExportTask.status, ["completed", "failed"]),
        ).to_list()

        deleted_count = 0
        for task in tasks_to_delete:
            if task.completed_at and task.completed_at.replace(tzinfo=timezone.utc) < cutoff_date:
                await task.delete()
                deleted_count += 1

        if deleted_count > 0:
            log.info("Cleaned up old export tasks", count=deleted_count)
