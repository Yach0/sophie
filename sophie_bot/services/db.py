from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from sophie_bot.config import Config
from sophie_bot.db.models import models


@dataclass(slots=True)
class DatabaseResources:
    mongo: AsyncMongoClient
    database: AsyncDatabase
    initialization_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    initialized: bool = False


@asynccontextmanager
async def open_database(config: Config) -> AsyncIterator[DatabaseResources]:
    mongo = AsyncMongoClient(config.mongo_host, config.mongo_port)
    try:
        resources = DatabaseResources(mongo=mongo, database=mongo[config.mongo_db])
        yield resources
    finally:
        await mongo.close()


async def init_db(database: AsyncDatabase, *, config: Config, skip_indexes: bool | None = None) -> None:
    """Initialize Beanie against the explicitly owned database."""
    if skip_indexes is None:
        skip_indexes = config.mongo_skip_indexes

    await init_beanie(
        database=database,
        document_models=models,
        allow_index_dropping=config.mongo_allow_index_dropping,
        skip_indexes=skip_indexes,
    )
