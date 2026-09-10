from __future__ import annotations

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.config import Config


def create_scheduler(config: Config) -> AsyncIOScheduler:
    mongo_store = MongoDBJobStore(
        database=config.mongo_db,
        collection="jobs",
        host=config.mongo_host,
        port=config.mongo_port,
    )
    return AsyncIOScheduler(jobstores={"default": mongo_store, "ram": MemoryJobStore()})
