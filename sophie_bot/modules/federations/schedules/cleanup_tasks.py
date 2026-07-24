from __future__ import annotations

from datetime import UTC, datetime, timedelta

from beanie.odm.operators.find.comparison import LT, Eq
from beanie.odm.operators.find.logical import And, Or

from sophie_bot.constants import FEDERATION_EXPORT_TTL_DAYS, FEDERATION_TASK_STALE_AFTER_MINUTES
from sophie_bot.db.models.federations import FederationTask
from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.modules.federations.utils.task_failure import notify_task_failed
from sophie_bot.utils.logger import log

_ORPHANED_TASK_ERROR = "The scheduler stopped while the task was running"


class CleanupOldTasks:
    """Scheduler job that reaps orphaned federation tasks and expires finished ones."""

    async def handle(self) -> None:
        """Reap orphaned tasks, then clean up old completed ones."""
        await self._fail_orphaned_tasks()
        await self._delete_expired_tasks()

    @staticmethod
    async def _fail_orphaned_tasks() -> None:
        """Fail tasks whose worker died mid-run, so their message never hangs forever.

        Only PENDING tasks are ever picked up, so a task left in PROCESSING by a restarted
        scheduler would otherwise sit untouched forever with its reply stuck on
        "Propagating…". Marking it FAILED both tells the user and makes it visible for a re-do.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=FEDERATION_TASK_STALE_AFTER_MINUTES)

        # Mongo's $lt is type-bracketed, so it never matches a null started_at. Fall back to
        # created_at for those, otherwise a PROCESSING task that somehow never recorded a start
        # time would be immortal - the exact "hangs forever" case this job exists to prevent.
        orphaned = await FederationTask.find(
            FederationTask.status == TaskStatus.PROCESSING,
            Or(
                LT(FederationTask.started_at, cutoff),
                And(Eq(FederationTask.started_at, None), LT(FederationTask.created_at, cutoff)),
            ),
        ).to_list()

        for task in orphaned:
            task.status = TaskStatus.FAILED
            task.error_message = _ORPHANED_TASK_ERROR
            task.completed_at = datetime.now(UTC)
            await task.save()
            await notify_task_failed(task, _ORPHANED_TASK_ERROR)

        if orphaned:
            log.warning("Failed orphaned federation tasks", count=len(orphaned))

    @staticmethod
    async def _delete_expired_tasks() -> None:
        """Delete old COMPLETED tasks.

        FAILED tasks are deliberately never deleted: they are the record of work that still
        needs investigating and re-doing, so they are kept indefinitely.
        """
        cutoff = datetime.now(UTC) - timedelta(days=FEDERATION_EXPORT_TTL_DAYS)

        result = await FederationTask.find(
            FederationTask.status == TaskStatus.COMPLETED,
            LT(FederationTask.completed_at, cutoff),
        ).delete()

        deleted_count = result.deleted_count if result else 0
        if deleted_count > 0:
            log.info("Cleaned up old federation tasks", count=deleted_count)
