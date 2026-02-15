from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

from beanie.odm.operators.find.comparison import In
from sophie_bot.db.models.federations import FederationExportTask
from sophie_bot.utils.feature_flags import FeatureType, is_enabled
from sophie_bot.utils.logger import log

# Constants
CSV_EXPORT_FEATURE_FLAG: Final[FeatureType] = "new_feds_fbanlist"


class CleanupOldExports:
    """Scheduler job to clean up old completed/failed export tasks."""

    async def handle(self) -> None:
        """Clean up export tasks older than TTL."""
        from sophie_bot.constants import FEDERATION_EXPORT_TTL_DAYS

        if not await is_enabled(CSV_EXPORT_FEATURE_FLAG):
            return

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
