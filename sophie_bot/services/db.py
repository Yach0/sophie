from typing import Optional

from beanie import init_beanie
from pymongo import AsyncMongoClient

from sophie_bot.config import CONFIG
from sophie_bot.db.models import models

async_mongo: AsyncMongoClient = AsyncMongoClient(CONFIG.mongo_host, CONFIG.mongo_port)
db = async_mongo[CONFIG.mongo_db]


async def init_db(skip_indexes: Optional[bool] = None):
    """Initialize Beanie and register migration tracking."""
    if skip_indexes is None:
        skip_indexes = CONFIG.mongo_skip_indexes

    await init_beanie(
        database=db,
        document_models=models,
        allow_index_dropping=CONFIG.mongo_allow_index_dropping,
        skip_indexes=skip_indexes,
    )
