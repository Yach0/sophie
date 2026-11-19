from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fakeredis import FakeAsyncRedis

from sophie_bot.db.models.ai.ai_evidence_memory import AIEvidenceMemoryModel, MemoryActions, MemoryFact, MemoryState
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.ai.schedules import write_memory
from sophie_bot.modules.ai.utils.ai_mode import set_chat_mode
from sophie_bot.modules.ai.utils.cache_messages import cache_message, get_cached_messages, get_context_epoch_key
from sophie_bot.modules.ai.utils.evidence_memory import (
    MemoryQuery,
    clear_memory,
    mutate_memory,
    prune_memory,
    read_memory,
    retrieve_memory,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override


async def test_cache_refreshes_retention_and_deduplicates_messages(test_redis: FakeAsyncRedis) -> None:
    now = datetime.now(UTC)
    await cache_message("Old", -100, 1, 1, now - timedelta(hours=47), None, redis=test_redis)
    await test_redis.expire("messages:-100", 10)
    await cache_message("New", -100, 1, 2, now, None, redis=test_redis)
    await cache_message("New", -100, 1, 2, now, None, redis=test_redis, eligible_for_memory=True)
    assert await test_redis.ttl("messages:-100") > 47 * 3600
    assert len(await get_cached_messages(-100, redis=test_redis)) == 2
    await cache_message("Expired", -100, 1, 3, now - timedelta(hours=49), None, redis=test_redis)
    assert {item.message_id for item in await get_cached_messages(-100, redis=test_redis)} == {1, 2}
    await test_redis.set(get_context_epoch_key(-100), 2)
    await cache_message("Obsolete", -100, 1, 4, now, None, redis=test_redis, expected_epoch=1)
    assert len(await get_cached_messages(-100, redis=test_redis)) == 2


def test_validated_fact_survives_expired_source_without_inventing_a_quote() -> None:
    now = datetime.now(UTC)
    fact = MemoryFact(
        speaker_tid=1,
        subject_tids=[1],
        scope="person",
        layer="personal",
        kind="preference",
        event="style",
        text="Prefers short replies",
        topic_id=None,
        branch_ids=[1],
        evidence_ids=[1],
        recorded_at=now - timedelta(days=3),
        occurred_at=now - timedelta(days=3),
    )
    state = MemoryState(facts=[fact])
    prune_memory(state, now - timedelta(days=30))
    evidence = retrieve_memory(state, MemoryQuery(person_tids=[1]))
    assert evidence.facts == [fact] and not evidence.sources
    assert evidence.unavailable_source_ids == [1] and not evidence.unknown_person_tids


async def test_writer_waits_for_ten_messages_and_limits_batch_to_sixty(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = test_services.redis
    now = datetime.now(UTC)
    chat = await ChatModel(
        tid=-1001150,
        type=ChatType.supergroup,
        first_name_or_title="Shared history",
        username=None,
        is_bot=False,
        last_saw=now,
    ).insert()
    chat = await ChatModel.get_by_tid(chat.tid)
    assert chat
    await set_chat_mode(chat, AIMode.entertainment, redis=redis)
    for flag in ("ai_memory_writer", "ai_memory_personal"):
        await set_chat_override(flag, chat.tid, True, redis=redis)
    runner = AsyncMock(return_value=SimpleNamespace(output=MemoryActions()))
    monkeypatch.setattr(write_memory, "run_structured_task", runner)
    monkeypatch.setattr(write_memory, "get_chat_model_plan", AsyncMock())
    monkeypatch.setattr(write_memory, "check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    for identity in range(1, 10):
        await cache_message("Preference", chat.tid, 1, identity, now, None, eligible_for_memory=True, redis=redis)
    worker = write_memory.WriteMemory(test_services)
    await worker.process_chat(chat)
    runner.assert_not_awaited()
    assert await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid) is None
    # Bots and ordinary cache rows cannot count toward the writer threshold.
    await cache_message("Bot", chat.tid, 2, 1000, now, None, is_bot=True, eligible_for_memory=True, redis=redis)
    await cache_message("Not opted in", chat.tid, 3, 1001, now, None, redis=redis)
    await worker.process_chat(chat)
    runner.assert_not_awaited()
    for identity in range(10, 66):
        await cache_message("Preference", chat.tid, 1, identity, now, None, eligible_for_memory=True, redis=redis)
    await worker.process_chat(chat)
    runner.assert_awaited_once()
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and len(document.state.processed_ids) == 60
    stored = await test_services.db.database["ai_evidence_memory"].find_one({"_id": document.id})
    assert stored and "sources" not in stored["state"]
    await worker.process_chat(chat)
    runner.assert_awaited_once()  # Five pending rows wait for a larger batch.
    await mutate_memory(chat, clear_memory, redis=redis)
    assert not (await read_memory(chat, now - timedelta(days=30), redis=redis)).sources
