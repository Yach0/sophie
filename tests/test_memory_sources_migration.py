from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from bson import DBRef, ObjectId
from fakeredis import FakeAsyncRedis
from redis.exceptions import RedisError

from sophie_bot.db.models.ai.ai_evidence_memory import MemorySource
from sophie_bot.modules.ai.utils.cache_messages import cache_message, get_cached_messages
from sophie_bot.services.db import DatabaseResources
from sophie_bot.services.migrations import MigrationResources, _bind_migration_resources
from tests.utils.mongo_mock import AsyncMongoMockClient

migration = importlib.import_module("sophie_bot.db.migrations.20261117_190031_unify_memory_sources_in_redis")


@pytest.fixture
async def resources(test_redis: FakeAsyncRedis) -> AsyncIterator[MigrationResources]:
    mongo = AsyncMongoMockClient()
    yield MigrationResources(
        database=DatabaseResources(mongo=mongo, database=mongo["memory_sources_migration"]), redis=test_redis
    )
    await mongo.aclose()


async def test_copy_then_remove_sources_preserves_facts_and_cache_metadata(resources: MigrationResources) -> None:
    now = datetime.now(UTC)
    database = resources.database.database
    chat_iid = ObjectId()
    await database["chats"].insert_one({"_id": chat_iid, "chat_id": -123})
    source = MemorySource(message_id=1, speaker_tid=10, text="Preference", sent_at=now, branch_id=1, processed=True)
    expired = source.model_copy(update={"message_id": 2, "sent_at": now - timedelta(hours=49)})
    excluded = source.model_copy(update={"message_id": 3, "speaker_tid": 20})
    await database["ai_evidence_memory"].insert_one(
        {
            "chat": DBRef("chats", chat_iid),
            "state": {
                "sources": [item.model_dump() for item in (source, expired, excluded)],
                "facts": [],
                "excluded_users": [20],
                "generation": "stable",
            },
        }
    )
    await cache_message("Preference", -123, 10, 1, now, "Name", proactively_reacted=True, redis=resources.redis)
    forward = _bind_migration_resources(migration.Forward.migrate, resources)
    await forward.run(session=None)
    await forward.run(session=None)
    document = await database["ai_evidence_memory"].find_one({})
    assert document and "sources" not in document["state"]
    assert document["state"]["generation"] == "stable"
    assert document["state"]["excluded_users"] == [20]
    assert document["state"]["processed_ids"] == [1]
    messages = await get_cached_messages(-123, redis=resources.redis)
    assert len(messages) == 1 and messages[0].eligible_for_memory and messages[0].proactively_reacted
    assert await resources.redis.ttl("messages:-123") <= 48 * 3600
    backward = _bind_migration_resources(migration.Backward.migrate, resources)
    await backward.run(session=None)
    await backward.run(session=None)
    document = await database["ai_evidence_memory"].find_one({})
    assert document and len(document["state"]["sources"]) == 1
    assert document["state"]["sources"][0]["processed"]
    await forward.run(session=None)
    assert len(await get_cached_messages(-123, redis=resources.redis)) == 1


async def test_redis_failure_never_deletes_mongo_sources(
    resources: MigrationResources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_iid = ObjectId()
    database = resources.database.database
    await database["chats"].insert_one({"_id": chat_iid, "chat_id": -123})
    source = MemorySource(message_id=1, speaker_tid=10, text="Keep", sent_at=datetime.now(UTC), branch_id=1)
    await database["ai_evidence_memory"].insert_one(
        {
            "chat": DBRef("chats", chat_iid),
            "state": {"sources": [source.model_dump()]},
        }
    )

    async def fail(*args: object, **kwargs: object) -> bool:
        raise RedisError("Unavailable")

    monkeypatch.setattr(migration, "store_cached_message", fail)
    with pytest.raises(RedisError):
        await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    document = await database["ai_evidence_memory"].find_one({})
    assert document and document["state"]["sources"][0]["text"] == "Keep"
