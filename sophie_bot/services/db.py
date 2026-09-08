from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from sophie_bot.config import CONFIG, Config
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
    resources = DatabaseResources(mongo=mongo, database=mongo[config.mongo_db])
    try:
        yield resources
    finally:
        await mongo.close()


def get_collection(database: AsyncDatabase, name: str) -> AsyncCollection[dict[str, Any]]:
    return database[name]


async def backfill_chat_admin_welcome_messages(
    chat_admin: AsyncCollection[dict[str, Any]],
    session: AsyncClientSession | None = None,
) -> int:
    """Repair administrator members saved before Telegram added the welcome permission."""
    result = await chat_admin.update_many(
        {"member.status": "administrator", "member.can_send_welcome_messages": {"$exists": False}},
        {"$set": {"member.can_send_welcome_messages": False}},
        session=session,
    )
    return result.modified_count


async def init_db(database: AsyncDatabase, *, skip_indexes: bool | None = None) -> None:
    """Initialize Beanie against the explicitly owned database."""
    if skip_indexes is None:
        skip_indexes = CONFIG.mongo_skip_indexes

    await init_beanie(
        database=database,
        document_models=models,
        allow_index_dropping=CONFIG.mongo_allow_index_dropping,
        skip_indexes=skip_indexes,
    )
