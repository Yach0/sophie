from typing import Any

from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from sophie_bot.config import CONFIG
from sophie_bot.db.models import models

async_mongo: AsyncMongoClient = AsyncMongoClient(CONFIG.mongo_host, CONFIG.mongo_port)
db = async_mongo[CONFIG.mongo_db]


def get_collection(name: str) -> AsyncCollection[dict[str, Any]]:
    """Collection handle resolved at call time.

    Migrations that touch collections with no Document model use this: unlike the module-level
    ``db``, it goes through whatever client ``async_mongo`` currently points at, which is what
    tests patch.
    """
    return async_mongo[CONFIG.mongo_db][name]


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


async def repair_legacy_database_data() -> None:
    """Apply idempotent repairs required before persisted models can be loaded."""
    await backfill_chat_admin_welcome_messages(get_collection("chat_admin"))


async def init_db(skip_indexes: bool | None = None) -> None:
    """Initialize Beanie and register migration tracking."""
    if skip_indexes is None:
        skip_indexes = CONFIG.mongo_skip_indexes

    await init_beanie(
        database=db,
        document_models=models,
        allow_index_dropping=CONFIG.mongo_allow_index_dropping,
        skip_indexes=skip_indexes,
    )
