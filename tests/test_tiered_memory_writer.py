from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RequestUsage, UsageLimits

from sophie_bot.db.models.ai.ai_evidence_memory import (
    MemoryFact,
    MemorySource,
    MemoryState,
)
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.ai.schedules import write_memory
from sophie_bot.modules.ai.utils import ai_tasks
from sophie_bot.modules.ai.utils.ai_mode import set_chat_mode
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_tokens import load_token_counter
from sophie_bot.modules.ai.utils.evidence_memory import capture_source, clear_memory, mutate_memory, read_memory
from sophie_bot.modules.ai.utils.memory_writer import (
    WRITER_DATA_TOKENS,
    RoutineMemoryOutput,
    WriterAction,
    WriterOutput,
    build_writer_prompt,
    deduplicate_batch,
    extract_memories,
)
from sophie_bot.modules.ai.utils.old_context import OldContext
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.ai_features import AI_FEATURE_MEMORY_WRITER
from sophie_bot.utils.feature_flags import set_chat_override
from tools.ai_memory_replay import ExpectedMemory

_CHAT_TIDS = count(-10011992, -1)


@pytest.mark.parametrize("invalid_fields", [{"event": " "}, {"speaker_tid": None}, {"evidence_ids": []}])
def test_writer_schema_rejects_unusable_actions(invalid_fields: dict[str, object]) -> None:
    schema = WriterAction.model_json_schema()
    assert {"event", "speaker_tid", "evidence_ids"} <= set(schema["required"])
    action = {
        "action": "ADD",
        "speaker_tid": 1,
        "subject_tids": [1],
        "scope": "person",
        "layer": "personal",
        "kind": "preference",
        "event": "reply length",
        "text": "Prefers short replies",
        "evidence_ids": [1],
    }
    WriterAction.model_validate(action)
    with pytest.raises(ValidationError):
        WriterAction.model_validate({**action, **invalid_fields})


def test_replay_accepts_group_participants_and_rejects_unrelated_subjects() -> None:
    expected = ExpectedMemory(
        speaker_tids={101, 102},
        optional_subject_tids={101, 102},
        evidence_ids={1, 2},
        kind="decision",
        layer="event",
    )
    at = datetime.now(UTC)
    fact = MemoryFact(
        speaker_tid=102,
        subject_tids=[],
        scope="group",
        layer="event",
        kind="decision",
        event="demo day",
        text="Demos on Friday",
        topic_id=None,
        branch_ids=[1],
        evidence_ids=[1, 2],
        occurred_at=at,
        recorded_at=at,
    )
    assert expected.matches(fact)
    assert expected.matches(fact.model_copy(update={"subject_tids": [101, 102]}))
    assert not expected.matches(fact.model_copy(update={"subject_tids": [101, 102, 103]}))
    assert not expected.matches(fact.model_copy(update={"evidence_ids": [2]}))


def _source(
    message_id: int, speaker_tid: int, text: str, *, branch: int, at: datetime, parent: int | None = None
) -> MemorySource:
    return MemorySource(
        message_id=message_id,
        speaker_tid=speaker_tid,
        speaker_name="",
        topic_id=48,
        branch_id=branch,
        reply_to_message_id=parent,
        sent_at=at,
        text=text,
    )


@pytest.fixture
async def writer_chat(test_services: ApplicationServices) -> ChatModel:
    redis = test_services.redis
    chat = await ChatModel(
        tid=next(_CHAT_TIDS),
        type=ChatType.supergroup,
        first_name_or_title="Writer test",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()
    chat = await ChatModel.get_by_tid(chat.tid)
    assert chat is not None
    await set_chat_mode(chat, AIMode.entertainment, redis=redis)
    for key in (
        "ai_memory_writer",
        "ai_memory_writer_tiered",
        "ai_memory_personal",
        "ai_memory_group",
        "ai_memory_shared_decisions",
    ):
        await set_chat_override(key, chat.tid, True, redis=redis)
    return chat


async def test_tiered_writer_escalates_only_group_evidence_and_claims_once(
    writer_chat: ChatModel, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis = test_services.redis
    at = datetime.now(UTC) - timedelta(hours=1)
    pending = [
        _source(9011, 711, "I prefer concise answers.", branch=4011, at=at),
        _source(9012, 711, "I prefer concise answers.", branch=4011, at=at),
        _source(9013, 712, "Let's hold demos on Friday.", branch=4013, at=at),
        _source(9014, 713, "Friday works. Let's do that.", branch=4013, at=at),
    ]

    def seed(state: MemoryState) -> None:
        for source in pending:
            capture_source(state, source)

    await mutate_memory(writer_chat, seed, redis=redis)
    monkeypatch.setattr(write_memory, "check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    model = OpenRouterModel(
        "test/writer",
        provider=OpenRouterProvider(api_key="test"),
        settings={"openrouter_provider": {"data_collection": "deny", "allow_fallbacks": False}},
    )
    plan = AIModelPlan((AIModelCandidate(model, model.model_name),))
    chosen_models: list[str] = []

    def select_model(name: str) -> AIModelPlan:
        chosen_models.append(name)
        return plan

    monkeypatch.setattr(write_memory, "single_model_plan", select_model)
    routine_started = asyncio.Event()
    release_routine = asyncio.Event()

    async def fake_run(
        task: ai_tasks.AIStructuredTask, _plan: AIModelPlan, history: OldContext, **kwargs: object
    ) -> SimpleNamespace:
        raw = history.prompt[0]
        assert isinstance(raw, str)
        assert "writer_attempts" not in raw and "9011" not in raw and "4011" not in raw
        assert kwargs["single_model"] is True
        assert task.model_settings == {
            "openrouter_provider": {"data_collection": "deny", "allow_fallbacks": False, "require_parameters": True}
        }
        payload = json.loads(raw)
        if task.output_type is RoutineMemoryOutput:
            assert len(payload["batch"]) == 3
            routine_started.set()
            await release_routine.wait()
            first, *hard = payload["batch"]
            return SimpleNamespace(
                output=RoutineMemoryOutput(
                    actions=[
                        WriterAction(
                            action="ADD",
                            scope="person",
                            layer="personal",
                            speaker_tid=first[1],
                            subject_tids=[first[1]],
                            evidence_ids=[first[0]],
                            event="reply style",
                            text="Prefers concise answers",
                            kind="preference",
                        )
                    ],
                    review=[entry[0] for entry in hard],
                )
            )
        first, second = payload["batch"]
        assert [entry[-1] for entry in payload["batch"]] == [pending[2].text, pending[3].text]
        return SimpleNamespace(
            output=WriterOutput(
                actions=[
                    WriterAction(
                        action="ADD",
                        scope="group",
                        layer="event",
                        kind="decision",
                        speaker_tid=first[1],
                        subject_tids=[],
                        evidence_ids=[first[0], second[0]],
                        event="demo schedule",
                        text="Demos on Friday",
                    )
                ]
            )
        )

    monkeypatch.setattr(write_memory, "run_structured_task", fake_run)
    worker = write_memory.WriteMemory(test_services)
    first_run = asyncio.create_task(worker.process_chat(writer_chat))
    try:
        await asyncio.wait_for(routine_started.wait(), timeout=3)
        await worker.process_chat(writer_chat)
        assert chosen_models == ["openai/gpt-6-luna"]
    finally:
        release_routine.set()
        await first_run
    state = await read_memory(writer_chat, at - timedelta(days=1), redis=redis)
    assert chosen_models == ["openai/gpt-6-luna", "gpt-6.1-terra"]
    assert [(fact.speaker_tid, fact.evidence_ids) for fact in state.facts] == [(711, [9011]), (712, [9013, 9014])]
    assert set(state.processed_ids) == {9011, 9012, 9013, 9014}
    assert not await redis.exists(f"ai:memory:writer:{writer_chat.tid}")


@pytest.mark.parametrize("reset", [False, True])
async def test_tiered_writer_avoids_review_after_noop_or_reset(
    writer_chat: ChatModel, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch, reset: bool
) -> None:
    redis = test_services.redis
    at = datetime.now(UTC) - timedelta(hours=1)
    await mutate_memory(
        writer_chat, lambda state: capture_source(state, _source(31, 721, "Hello!", branch=31, at=at)), redis=redis
    )
    monkeypatch.setattr(write_memory, "check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    monkeypatch.setattr(
        write_memory,
        "single_model_plan",
        lambda name: AIModelPlan((AIModelCandidate(FunctionModel(lambda *_: ModelResponse(parts=[])), name),)),
    )

    async def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        if reset:
            await mutate_memory(writer_chat, clear_memory, redis=redis)
        return SimpleNamespace(output=RoutineMemoryOutput(actions=[], review=[1] if reset else []))

    runner = AsyncMock(side_effect=run)
    monkeypatch.setattr(write_memory, "run_structured_task", runner)
    await write_memory.WriteMemory(test_services).process_chat(writer_chat)
    runner.assert_awaited_once()
    state = await read_memory(writer_chat, at - timedelta(days=1), redis=redis)
    assert not state.facts
    assert state.processed_ids == ([] if reset else [31])


async def test_prompt_budget_and_duplicates_preserve_independent_evidence() -> None:
    at = datetime.now(UTC)
    original = _source(90101, 81102, "Yes, we agree.", branch=2, at=at, parent=2)
    repeat = original.model_copy(update={"message_id": 90102})
    independent = repeat.model_copy(update={"message_id": 90103, "speaker_tid": 81103})
    separate_reply = repeat.model_copy(update={"message_id": 90104, "reply_to_message_id": 3})
    sources = [original, repeat, independent, separate_reply]
    assert deduplicate_batch(sources, sources) == ([original, independent, separate_reply], {90102: 90101})
    parent = _source(2, 81105, "Consider Friday.", branch=2, at=at)
    outlier = original.model_copy(update={"text": "🪴" * 9_000})
    state = MemoryState(sources=[parent, outlier, independent])
    counter = await load_token_counter("gpt-4")
    prompt = build_writer_prompt(state, [outlier, independent], counter)
    assert counter.count_text(prompt.text) <= WRITER_DATA_TOKENS
    assert prompt.batch_ids == {90101}
    assert "[message clipped]" in prompt.text
    assert "Consider Friday." in prompt.text
    assert "81102" not in prompt.text
    with pytest.raises(ValueError, match="outside its prompt"):
        prompt.decode_actions(
            [
                WriterAction(
                    action="ADD",
                    speaker_tid=81102,
                    subject_tids=[1],
                    scope="person",
                    layer="personal",
                    event="demo schedule",
                    kind="preference",
                    text="Prefers Friday",
                    evidence_ids=[1],
                )
            ]
        )


async def test_routine_add_that_matches_a_current_event_goes_to_review() -> None:
    at = datetime.now(UTC)
    source = _source(90112, 81202, "I prefer evenings now.", branch=4, at=at)
    previous = MemoryFact(
        speaker_tid=source.speaker_tid,
        subject_tids=[source.speaker_tid],
        scope="person",
        layer="personal",
        kind="preference",
        event="study time",
        text="Prefers mornings",
        topic_id=source.topic_id,
        branch_ids=[source.branch_id],
        evidence_ids=[90111],
        recorded_at=at - timedelta(days=1),
        occurred_at=at - timedelta(days=1),
    )
    state = MemoryState(sources=[source], facts=[previous])
    candidate = WriterAction(
        action="ADD",
        scope="person",
        layer="personal",
        speaker_tid=1,
        subject_tids=[1],
        evidence_ids=[1],
        event="study time",
        text="Prefers evenings",
        kind="preference",
    )
    routine = AsyncMock(return_value=RoutineMemoryOutput(actions=[candidate], review=[]))
    review = AsyncMock(
        return_value=WriterOutput(
            actions=[
                candidate.model_copy(update={"action": "UPDATE", "target_id": "1"}),
            ]
        )
    )
    result, processed = await extract_memories(
        state,
        [source],
        await load_token_counter("gpt-4"),
        routine=routine,
        review=review,
    )
    review.assert_awaited_once()
    assert processed == {source.message_id}
    assert len(result.actions) == 1
    assert result.actions[0].action == "UPDATE" and result.actions[0].target_id == previous.fact_id


async def test_structured_task_accounts_for_schema_retry_before_validation(
    writer_chat: ChatModel, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def respond(_messages: Sequence[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert info.output_tools
        arguments = {"actions": "invalid"} if calls == 1 else {"actions": [], "review": []}
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, arguments)],
            usage=RequestUsage(input_tokens=20, output_tokens=5),
        )

    model = FunctionModel(respond)
    plan = AIModelPlan((AIModelCandidate(model, model.model_name),))
    charge = AsyncMock()
    observer = AsyncMock()
    monkeypatch.setattr(ai_tasks, "charge_ai_usage", charge)
    history = OldContext(services=test_services)
    history.prompt = ["Read this chat."]
    result = await ai_tasks.run_structured_task(
        ai_tasks.AIStructuredTask(RoutineMemoryOutput, "memory:writer:routine", AI_FEATURE_MEMORY_WRITER),
        plan,
        history,
        chat_iid=writer_chat.iid,
        redis=test_services.redis,
        single_model=True,
        usage_limits=UsageLimits(request_limit=2),
        on_response=observer,
    )
    assert result.output == RoutineMemoryOutput(actions=[], review=[])
    assert calls == observer.await_count == charge.await_count == 2
    assert all(
        call.args[1] == AI_FEATURE_MEMORY_WRITER and call.args[3].total_tokens == 25 for call in charge.await_args_list
    )
