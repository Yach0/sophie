from __future__ import annotations

from typing import cast

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.config import CONFIG
from sophie_bot.utils.runtime_proxy import RuntimeProxy


def create_scheduler() -> AsyncIOScheduler:
    mongo_store = MongoDBJobStore(
        database=CONFIG.mongo_db,
        collection="jobs",
        host=CONFIG.mongo_host,
        port=CONFIG.mongo_port,
    )
    mem_store = MemoryJobStore()
    return AsyncIOScheduler(jobstores={"default": mongo_store, "ram": mem_store})


_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler

    if _scheduler is None:
        _scheduler = create_scheduler()

    return _scheduler


def set_scheduler(active_scheduler: AsyncIOScheduler) -> AsyncIOScheduler:
    global _scheduler

    _scheduler = active_scheduler
    return active_scheduler


scheduler = cast(AsyncIOScheduler, RuntimeProxy(get_scheduler))
