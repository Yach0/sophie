from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, OperationFailure

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

    if not skip_indexes:
        await ensure_ws_user_uniqueness(database)

    await init_beanie(
        database=database,
        document_models=models,
        allow_index_dropping=config.mongo_allow_index_dropping,
        skip_indexes=skip_indexes,
    )


async def ensure_ws_user_uniqueness(database: AsyncDatabase) -> None:
    """Require correctness even when general index synchronization is disabled.

    Do not discard historical duplicates: their durable outcomes require review.
    MongoDB validates existing data and concurrent inserts during index creation.
    """
    try:
        await database["ws_users"].create_index([("user", 1), ("group", 1)], unique=True, name="ws_user_group_unique")
    except (DuplicateKeyError, OperationFailure) as error:
        raise RuntimeError(
            "Cannot enforce Welcome Security user/group uniqueness. Stop all WS writers, "
            "back up and reconcile duplicate ws_users (including durable transitions), "
            "then create ws_user_group_unique before restarting. No rows were removed."
        ) from error
