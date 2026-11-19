from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from sophie_bot.db.models.ai.ai_evidence_memory import AIEvidenceMemoryModel, MemoryFact, StoredMemoryState
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.modules.ai.schedules.write_memory import WriteMemory
from sophie_bot.modules.ai.utils.evidence_memory import (
    MEMORY_POLICY_MIGRATION,
    memory_cutoff,
    require_memory_policy_migration,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.migrations import (
    MigrationResources,
    _bind_migration_resources,
    _run_single_migration,
    run_migration_backward,
)
from sophie_bot.utils.feature_flags import is_enabled

migration = importlib.import_module(f"sophie_bot.db.migrations.{MEMORY_POLICY_MIGRATION}")


@pytest.fixture(autouse=True)
async def clean_policies(test_services: ApplicationServices) -> AsyncIterator[None]:
    db = test_services.db.database
    flags = ("ai_memory_capture", "ai_memory_writer", "ai_memory_retention_days")
    query = {"feature": {"$in": [*flags, *("__rollout__:" + flag for flag in flags)]}}
    await db["feature_flag_overrides"].delete_many(query)
    yield
    await db["feature_flag_overrides"].delete_many(query)
    await db["ai_memory_retention_policy"].delete_many({})
    await db["ai_memory_policy_migration_backup"].delete_many({})
    await db["migration_states"].delete_many({"name": MEMORY_POLICY_MIGRATION})
    await db["chats"].delete_many({"chat_id": {"$in": [-10011360, -10011361, -10011362]}})


async def test_preserves_effective_memory_policies_and_rolls_back(test_services: ApplicationServices) -> None:
    redis = test_services.redis
    db = test_services.db.database
    now = datetime.now(UTC)

    async def seed_chat(chat_tid: int) -> ChatModel:
        chat = await ChatModel(
            tid=chat_tid,
            type=ChatType.supergroup,
            first_name_or_title="Memory migration",
            username=None,
            is_bot=False,
            last_saw=now,
        ).insert()
        chat = await ChatModel.get_by_tid(chat_tid)
        assert chat
        fact = MemoryFact(
            scope="person",
            layer="personal",
            kind="fact",
            event="retention",
            text="Stored fact",
            speaker_tid=41,
            subject_tids=[41],
            topic_id=None,
            branch_ids=[1],
            evidence_ids=[1],
            occurred_at=now - timedelta(days=45),
            recorded_at=now - timedelta(days=45),
        )
        await AIEvidenceMemoryModel(chat=chat, state=StoredMemoryState(facts=[fact])).insert()
        return chat

    writer_only = await seed_chat(-10011360)
    eligible = await seed_chat(-10011361)
    await FeatureFlagOverride.set_override("ai_memory_writer", True)
    await FeatureFlagOverride.set_override("ai_memory_capture", True, chat_tid=eligible.tid)
    await FeatureFlagOverride.set_override("ai_memory_retention_days", 90)
    await FeatureFlagOverride.set_override("ai_memory_retention_days", 7, chat_tid=eligible.tid)
    await redis.hset("sophie:kill_switch", "ai_memory_writer", "1")
    await redis.hset(f"sophie:kill_switch_chat:{writer_only.tid}", "ai_memory_writer", "1")

    resources = MigrationResources(database=test_services.db, redis=redis)
    forward = _bind_migration_resources(migration.Forward.migrate, resources)
    with pytest.raises(RuntimeError, match="Run the AI memory policy migration"):
        await require_memory_policy_migration()
    await _run_single_migration(MEMORY_POLICY_MIGRATION, resources)
    await require_memory_policy_migration()
    await forward.run(session=None)
    assert not await is_enabled("ai_memory_writer", chat_tid=writer_only.tid, redis=redis)
    assert await is_enabled("ai_memory_writer", chat_tid=eligible.tid, redis=redis)
    assert (now - await memory_cutoff(writer_only.tid)).days == 89
    assert (now - await memory_cutoff(eligible.tid)).days == 6
    assert await db["ai_memory_retention_policy"].count_documents({}) == 2
    worker = WriteMemory(test_services)
    await worker.process_chat(writer_only)
    await worker.process_chat(eligible)
    long_retention = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == writer_only.iid)
    short_retention = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == eligible.iid)
    assert long_retention and long_retention.state.facts
    assert short_retention and not short_retention.state.facts

    backward = _bind_migration_resources(migration.Backward.migrate, resources)
    await run_migration_backward(MEMORY_POLICY_MIGRATION, resources)
    await backward.run(session=None)
    restored = await FeatureFlagOverride.get_override("ai_memory_writer")
    assert restored and restored.value is True
    assert await FeatureFlagOverride.get_override("ai_memory_writer", chat_tid=eligible.tid) is None
    assert await db["ai_memory_retention_policy"].count_documents({}) == 0
    assert await FeatureFlagOverride.get_override("ai_memory_retention_days", chat_tid=eligible.tid)


async def test_snapshots_active_rollouts_without_enabling_unknown_chats(test_services: ApplicationServices) -> None:
    db = test_services.db.database
    redis = test_services.redis
    chat_tid = -10011362
    await db["chats"].insert_one({"chat_id": chat_tid})
    rollout = {
        "start_percentage": 100,
        "target_percentage": 100,
        "started_at": datetime.now(UTC).isoformat(),
        "duration_days": None,
    }
    for flag, value in (("ai_memory_capture", True), ("ai_memory_writer", True), ("ai_memory_retention_days", 60)):
        await FeatureFlagOverride.set_override("__rollout__:" + flag, {**rollout, "value": value})

    resources = MigrationResources(database=test_services.db, redis=redis)
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    assert await is_enabled("ai_memory_writer", chat_tid=chat_tid, redis=redis)
    assert not await is_enabled("ai_memory_writer", chat_tid=chat_tid - 1, redis=redis)
    assert await db["ai_memory_retention_policy"].find_one({"chat_tid": chat_tid, "days": 60})
    assert await FeatureFlagOverride.get_override("__rollout__:ai_memory_writer") is None
