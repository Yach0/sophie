from __future__ import annotations

from datetime import datetime, timedelta, timezone

from beanie.odm.operators.find.comparison import LT, In

from sophie_bot.constants import FEDERATION_EXPORT_TTL_DAYS
from sophie_bot.db.models.federations import FederationTask
from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.utils.logger import log


class CleanupOldTasks:
    """Scheduler job to clean up old completed/failed federation tasks."""

    async def handle(self) -> None:
        """Clean up finished federation tasks older than TTL."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=FEDERATION_EXPORT_TTL_DAYS)

        result = await FederationTask.find(
            In(FederationTask.status, [TaskStatus.COMPLETED, TaskStatus.FAILED]),
            LT(FederationTask.completed_at, cutoff_date),
        ).delete()

        deleted_count = result.deleted_count if result else 0
        if deleted_count > 0:
            log.info("Cleaned up old federation tasks", count=deleted_count)
