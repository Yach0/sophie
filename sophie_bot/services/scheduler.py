from __future__ import annotations

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sophie_bot.config import CONFIG

mongo_store = MongoDBJobStore(
    database=CONFIG.mongo_db,
    collection="jobs",
    host=CONFIG.mongo_host,
    port=CONFIG.mongo_port,
)
mem_store = MemoryJobStore()
scheduler = AsyncIOScheduler(jobstores={"default": mongo_store, "ram": mem_store})

# Deprecated: scheduler_loop is no longer used. The scheduler now runs on the main event loop.
scheduler_loop = None
