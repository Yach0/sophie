from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId
from fakeredis import FakeAsyncRedis
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from sophie_bot.db.models.ai.ai_chat_summary import AIChatSummaryModel
from sophie_bot.db.models.ai.ai_evidence_memory import (
    AIEvidenceMemoryModel,
    MemoryAction,
    MemoryActions,
    MemorySource,
    MemoryState,
)
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.ai.schedules import write_memory
from sophie_bot.modules.ai.schedules.generate_chat_summaries import GenerateChatSummaries
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities, set_chat_mode
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import cache_message, get_context_epoch_key
from sophie_bot.modules.ai.utils.chatbot_agent import build_chatbot_usage_limits, get_chatbot_tools
from sophie_bot.modules.ai.utils.chatbot_tool_history import get_tool_exchanges, store_tool_exchanges
from sophie_bot.modules.ai.utils.evidence_memory import (
    MemoryQuery,
    apply_actions,
    capture_source,
    clear_memory,
    memory_context,
    mutate_memory,
    prune_memory,
    read_memory,
    retrieve_memory,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override

START = datetime(2026, 11, 1, tzinfo=UTC)


def source(
    identity: int, speaker: int = 1, text: str = "Picnic Friday", topic: int = 10, reply: int | None = None
) -> MemorySource:
    return MemorySource(
        message_id=identity,
        speaker_tid=speaker,
        text=text,
        sent_at=START + timedelta(minutes=identity),
        topic_id=topic,
        branch_id=reply or identity,
        reply_to_message_id=reply,
    )


def action(identity: int, speaker: int = 1, **values: object) -> MemoryAction:
    return MemoryAction.model_validate(
        {
            "action": "ADD",
            "speaker_tid": speaker,
            "subject_tids": [speaker],
            "event": "picnic",
            "text": "Picnic Friday",
            "evidence_ids": [identity],
            **values,
        }
    )


def write(state: MemoryState, actions: list[MemoryAction], identities: set[int], minute: int = 10) -> bool:
    return apply_actions(
        state, MemoryActions(actions=actions), identities, state.generation, START + timedelta(minutes=minute)
    )


def test_replay_attribution_dissent_and_unknown() -> None:
    state = MemoryState()
    capture_source(state, source(1, text="Bob agreed to Friday"))
    capture_source(state, source(2, speaker=2, text="No, I can only do Saturday", reply=1))
    write(
        state,
        [action(1, layer="observation", subject_tids=[2]), action(2, speaker=2, kind="disputed", text="Saturday only")],
        {1, 2},
    )
    result = retrieve_memory(state, MemoryQuery(event="picnic", person_tids=[1, 2, 3]))
    assert [(fact.speaker_tid, fact.subject_tids, fact.kind) for fact in result.facts] == [
        (1, [2], "fact"),
        (2, [2], "disputed"),
    ]
    assert result.unknown_person_tids == [3]
    assert {item.message_id for item in result.sources} == {1, 2}


def test_report_about_quiet_member_does_not_establish_their_position() -> None:
    state = MemoryState(sources=[source(1), source(2, speaker=2, text="hello")])
    write(state, [action(1, layer="observation", subject_tids=[2])], {1})
    result = retrieve_memory(state, MemoryQuery(event="picnic", person_tids=[2]))
    assert result.unknown_person_tids == [2]
    assert result.facts[0].speaker_tid == 1


def test_replay_changed_plan_and_historical_state() -> None:
    state = MemoryState(sources=[source(1)])
    write(state, [action(1)], {1}, minute=2)
    original = state.facts[0]
    capture_source(state, source(3, text="Actually Saturday", reply=1))
    write(state, [action(3, action="UPDATE", target_id=original.fact_id, text="Picnic Saturday")], {3}, minute=4)
    assert retrieve_memory(state, MemoryQuery()).facts == [state.facts[1]]
    assert retrieve_memory(state, MemoryQuery(as_of=START + timedelta(minutes=3))).facts == [original]
    assert len(retrieve_memory(state, MemoryQuery(history=True)).facts) == 2
    assert state.facts[1].previous_id == original.fact_id


@pytest.mark.parametrize(
    "changes",
    [
        {"speaker_tid": 3},
        {"subject_tids": [999]},
        {"scope": "group"},
        {"evidence_ids": [999]},
        {"evidence_ids": []},
        {"text": " "},
        {"kind": "decision"},
        {"layer": "norm", "scope": "group"},
    ],
)
def test_reject_invalid_actions_atomically(changes: dict[str, object]) -> None:
    state = MemoryState(sources=[source(1)])
    with pytest.raises(ValueError):
        write(state, [action(1), action(1, **changes)], {1})
    assert not state.facts
    assert not state.sources[0].processed


def test_replay_overlapping_topics_and_event_identity() -> None:
    state = MemoryState()
    for item in (source(1), source(2, topic=20), source(3, speaker=2, reply=1)):
        capture_source(state, item)
    write(state, [action(1), action(2, event="workshop"), action(3, speaker=2, kind="proposal")], {1, 2, 3})
    result = retrieve_memory(state, MemoryQuery(topic_id=10, event="picnic", branch_id=1))
    assert len(result.facts) == 2
    assert all(item.topic_id == 10 and item.branch_id == 1 for item in result.sources)
    assert not retrieve_memory(state, MemoryQuery(event="unknown")).sources


def test_quiet_member_has_reserved_evidence_slot() -> None:
    state = MemoryState(sources=[source(identity) for identity in range(1, 51)])
    state.sources.append(source(51, speaker=2, text="Picnic no nuts"))
    result = retrieve_memory(state, MemoryQuery(text="picnic", person_tids=[1, 2, 3]))
    assert any(item.speaker_tid == 2 for item in result.sources)
    assert result.unknown_person_tids == [3]


def test_retention_deletes_dependent_versions_and_sources() -> None:
    state = MemoryState(sources=[source(1)])
    write(state, [action(1)], {1})
    prune_memory(state, START + timedelta(days=1))
    assert not state.sources and not state.facts


def test_edit_invalidates_derived_facts_and_pending_writer() -> None:
    state = MemoryState(sources=[source(1)])
    write(state, [action(1)], {1})
    generation = state.generation
    capture_source(state, source(1, text="Correction"))
    assert not state.facts
    assert not apply_actions(state, MemoryActions(actions=[action(1)]), {1}, generation, START)


def test_forget_fences_writer_and_preserves_opt_out() -> None:
    state = MemoryState(sources=[source(1)], excluded_users=[2])
    generation = state.generation
    clear_memory(state)
    assert not apply_actions(state, MemoryActions(actions=[action(1)]), {1}, generation, START)
    capture_source(state, source(2, speaker=2))
    assert not state.sources


def test_update_cannot_replace_other_speaker_or_old_head() -> None:
    state = MemoryState(sources=[source(1)])
    write(state, [action(1)], {1})
    original = state.facts[0]
    capture_source(state, source(2, speaker=2, reply=1))
    with pytest.raises(ValueError):
        write(state, [action(2, speaker=2, action="UPDATE", target_id=original.fact_id)], {2})
    capture_source(state, source(3, reply=1))
    write(state, [action(3, action="UPDATE", target_id=original.fact_id)], {3})
    capture_source(state, source(4, reply=1))
    with pytest.raises(ValueError):
        write(state, [action(4, action="UPDATE", target_id=original.fact_id)], {4})


async def test_db_roundtrip_boundaries_revision_and_utc(db_init: object, test_redis: FakeAsyncRedis) -> None:
    chat = await ChatModel(
        tid=-1001122,
        type=ChatType.supergroup,
        first_name_or_title="Memory",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()
    other = await ChatModel(
        tid=-1001123,
        type=ChatType.supergroup,
        first_name_or_title="Memory",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()
    chat = await ChatModel.get_by_tid(-1001122)
    other = await ChatModel.get_by_tid(-1001123)
    assert chat and other
    await mutate_memory(chat, lambda state: capture_source(state, source(1)))
    state = await read_memory(chat.iid, START)
    assert state.sources[0].sent_at.tzinfo is not None
    assert not (await read_memory(other.iid, START)).sources
    await set_chat_override("ai_memory_retrieval", chat.tid, True, redis=test_redis)
    assert "Picnic" in await memory_context(chat, MemoryQuery(), redis=test_redis)
    await set_chat_mode(chat, AIMode.support, redis=test_redis)
    assert not (await read_memory(chat.iid, START)).sources


async def test_independent_writer_and_stale_result(
    db_init: object,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = await ChatModel(
        tid=-1001124,
        type=ChatType.supergroup,
        first_name_or_title="Memory",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()
    chat = await ChatModel.get_by_tid(-1001124)
    assert chat
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    for flag in ("ai_memory_writer", "ai_memory_personal"):
        await set_chat_override(flag, chat.tid, True, redis=test_services.redis)
    now = datetime.now(UTC)
    await mutate_memory(chat, lambda state: capture_source(state, source(1).model_copy(update={"sent_at": now})))
    monkeypatch.setattr(write_memory, "get_chat_model_plan", AsyncMock())
    monkeypatch.setattr(write_memory, "check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    runner = AsyncMock(return_value=SimpleNamespace(output=MemoryActions(actions=[action(1)])))
    monkeypatch.setattr(write_memory, "run_structured_task", runner)
    worker = write_memory.WriteMemory(test_services)
    await worker.handle()
    document = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat.iid)
    assert document and document.state.facts
    await worker.handle()
    assert runner.await_count == 1
    await mutate_memory(chat, lambda state: capture_source(state, source(2).model_copy(update={"sent_at": now})))

    async def forget_during_write(*args: object, **kwargs: object) -> SimpleNamespace:
        await mutate_memory(chat, clear_memory)
        return SimpleNamespace(output=MemoryActions(actions=[action(2)]))

    runner.side_effect = forget_during_write
    await worker.handle()
    assert not (await read_memory(chat.iid, START)).facts


async def test_rollout_preserves_legacy_tools_until_replacement_is_enabled(
    db_init: object,
    test_services: ApplicationServices,
) -> None:
    chat = await ChatModel(
        tid=-1001130,
        type=ChatType.supergroup,
        first_name_or_title="Rollout",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()
    chat = await ChatModel.get_by_tid(-1001130)
    assert chat
    context = SophieAIToolContext(
        connection=SimpleNamespace(db_model=chat),
        chat_tid=chat.tid,
        chat_iid=chat.iid,
        services=test_services,
        mode=AIMode.entertainment,
    )
    capabilities = get_capabilities(AIMode.entertainment)
    await set_chat_override("ai_memory_capture", chat.tid, True, redis=test_services.redis)
    names = {tool.name for tool in await get_chatbot_tools(context, capabilities)}
    assert {"write_memory", "forget_memory"} <= names
    assert "search_memory" not in names
    await set_chat_override("ai_memory_retrieval", chat.tid, True, redis=test_services.redis)
    names = {tool.name for tool in await get_chatbot_tools(context, capabilities)}
    assert {"write_memory", "forget_memory", "search_memory"} <= names
    await set_chat_override("ai_memory_replace_legacy", chat.tid, True, redis=test_services.redis)
    names = {tool.name for tool in await get_chatbot_tools(context, capabilities)}
    assert "search_memory" in names
    assert not {"write_memory", "forget_memory"} & names


def test_reply_anchor_and_attribution_filters() -> None:
    state = MemoryState(sources=[source(1), source(2, topic=20)])
    write(state, [action(1), action(2, event="other")], {1, 2})
    assert len(retrieve_memory(state, MemoryQuery(anchor_id=1)).facts) == 1
    assert not retrieve_memory(state, MemoryQuery(speaker_tid=99)).facts
    assert not retrieve_memory(state, MemoryQuery(scope="group")).facts


async def test_tool_history_cannot_restore_deleted_memory_context(
    test_services: ApplicationServices,
) -> None:
    redis = test_services.redis
    messages = [ModelRequest(parts=[ToolReturnPart(tool_name="search_memory", tool_call_id="read", content="secret")])]
    await redis.set(get_context_epoch_key(-12345), 2)
    await store_tool_exchanges(-12345, 1, messages, redis=redis, expected_epoch=1)
    assert not await get_tool_exchanges(-12345, redis=redis)


async def test_summary_discards_output_after_memory_reset(
    db_init: object,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = await ChatModel(
        tid=-1001131,
        type=ChatType.supergroup,
        first_name_or_title="Privacy",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()
    chat = await ChatModel.get_by_tid(-1001131)
    assert chat
    await set_chat_override("ai_memory_controls", chat.tid, True, redis=test_services.redis)
    now = datetime.now(UTC)
    for identity in range(1, 4):
        await cache_message("Discussion", chat.tid, identity, identity, now, None, redis=test_services.redis)

    async def generate(*args: object, **kwargs: object) -> SimpleNamespace:
        await test_services.redis.incr(get_context_epoch_key(chat.tid))
        return SimpleNamespace(overview="Obsolete", lines=[])

    worker = GenerateChatSummaries(test_services)
    monkeypatch.setattr(worker, "generate_verified_summary_groups", AsyncMock(side_effect=generate))
    await worker.process_chat(chat, now.date(), now=now)
    assert await AIChatSummaryModel.get_latest(chat.iid) is None


@pytest.mark.parametrize(
    ("mode", "retrieval", "request_limit", "tool_calls_limit"),
    [
        (AIMode.entertainment, False, 4, 6),
        (AIMode.entertainment, True, 8, 12),
        (AIMode.support, True, 4, 6),
    ],
)
async def test_agentic_memory_turn_budget_is_limited_to_active_retrieval(
    test_services: ApplicationServices,
    mode: AIMode,
    retrieval: bool,
    request_limit: int,
    tool_calls_limit: int,
) -> None:
    chat_tid = -1001140
    await set_chat_override("ai_memory_retrieval", chat_tid, retrieval, redis=test_services.redis)
    await set_chat_override("ai_memory_capture", chat_tid, True, redis=test_services.redis)
    context = SophieAIToolContext(
        connection=SimpleNamespace(),
        chat_tid=chat_tid,
        chat_iid=PydanticObjectId(),
        services=test_services,
        mode=mode,
    )
    limits = await build_chatbot_usage_limits(context)
    assert limits.request_limit == request_limit
    assert limits.tool_calls_limit == tool_calls_limit


@pytest.mark.parametrize(("request_limit", "tool_calls_limit", "expected"), [(10, 18, (10, 18)), (0, -1, (8, 12))])
async def test_agentic_memory_budget_overrides_and_safe_defaults(
    test_services: ApplicationServices,
    request_limit: int,
    tool_calls_limit: int,
    expected: tuple[int, int],
) -> None:
    chat_tid = -1001141
    redis = test_services.redis
    await set_chat_override("ai_memory_retrieval", chat_tid, True, redis=redis)
    await set_chat_override("ai_memory_request_limit", chat_tid, request_limit, redis=redis)
    await set_chat_override("ai_memory_tool_calls_limit", chat_tid, tool_calls_limit, redis=redis)
    await set_chat_override("ai_chatbot_request_limit", chat_tid, 3, redis=redis)
    await set_chat_override("ai_chatbot_tool_calls_limit", chat_tid, 5, redis=redis)
    await set_chat_override("ai_chatbot_response_tokens_limit", chat_tid, 2048, redis=redis)
    context = SophieAIToolContext(
        connection=SimpleNamespace(),
        chat_tid=chat_tid,
        chat_iid=PydanticObjectId(),
        services=test_services,
        mode=AIMode.entertainment,
    )
    limits = await build_chatbot_usage_limits(context)
    assert (limits.request_limit, limits.tool_calls_limit) == expected
    assert limits.output_tokens_limit == 2048
    await set_chat_override("ai_memory_retrieval", chat_tid, False, redis=redis)
    legacy_limits = await build_chatbot_usage_limits(context)
    assert (legacy_limits.request_limit, legacy_limits.tool_calls_limit) == (3, 5)
