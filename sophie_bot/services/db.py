from __future__ import annotations

from typing import Optional

import pymongo.errors
from beanie import init_beanie
from pymongo import AsyncMongoClient

from sophie_bot.config import CONFIG
from sophie_bot.db.models import models
from sophie_bot.utils.logger import log

async_mongo: AsyncMongoClient = AsyncMongoClient(CONFIG.mongo_host, CONFIG.mongo_port)
db = async_mongo[CONFIG.mongo_db]

# Old index names that conflict with current model definitions.
# Safe to remove on every startup — they are either non-unique versions of
# now-unique indexes, or indexes on fields that are always null (DBRef $id).
_STALE_INDEXES_TO_DROP = {
    "users_in_groups": [
        "user.$id_1_group.$id_1",
        "user.id_1_group.id_1",
        "user_group_ref_key",
    ],
}


async def _drop_stale_indexes() -> None:
    """Drop old indexes that conflict with current model definitions."""
    for collection_name, index_names in _STALE_INDEXES_TO_DROP.items():
        collection = db[collection_name]
        for index_name in index_names:
            try:
                await collection.drop_index(index_name)
                log.info("Dropped stale index", collection=collection_name, index=index_name)
            except pymongo.errors.OperationFailure:
                # Index doesn't exist — nothing to drop.
                pass


async def init_db(skip_indexes: Optional[bool] = None) -> None:
    """Initialize Beanie and register migration tracking."""
    if skip_indexes is None:
        skip_indexes = CONFIG.mongo_skip_indexes

    if not skip_indexes:
        await _drop_stale_indexes()

    await init_beanie(
        database=db,
        document_models=models,
        allow_index_dropping=CONFIG.mongo_allow_index_dropping,
        skip_indexes=skip_indexes,
    )
