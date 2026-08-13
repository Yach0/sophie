from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Iterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from pydantic import BaseModel
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_model_plan import (
    AIModelCandidate,
    AIModelPlan,
    build_model_plan,
    request_has_images,
)
from sophie_bot.modules.ai.utils.ai_refusal import is_refusal_output
from sophie_bot.modules.ai.utils.ai_run import (
    AIRequestOptions,
    ChatbotStreamOptions,
    build_model_settings,
    run_ai_stream,
    run_ai_structured,
    run_ai_text,
)


class StructuredOutput(BaseModel):
    value: str


@pytest.fixture
def no_stream_debounce(monkeypatch: Any) -> Iterator[None]:
    """Emit every accumulated update, so assertions see the full callback sequence."""
    monkeypatch.setattr(ai_run, "_STREAM_DEBOUNCE_SECONDS", 0.0)
    yield


def _part_round(part_cls: Any, delta_cls: Any, chunks: tuple[str, ...]) -> list[Any]:
    """Events for one model response that streams a single part of the given kind."""
    return [
        PartStartEvent(index=0, part=part_cls(content="")),
        *(PartDeltaEvent(index=0, delta=delta_cls(content_delta=chunk)) for chunk in chunks),
        PartEndEvent(index=0, part=part_cls(content="".join(chunks))),
    ]


def text_round(*chunks: str) -> list[Any]:
    return _part_round(TextPart, TextPartDelta, chunks)


def thinking_round(*chunks: str) -> list[Any]:
    return _part_round(ThinkingPart, ThinkingPartDelta, chunks)


def tool_call(tool_name: str) -> FunctionToolCallEvent:
    return FunctionToolCallEvent(part=ToolCallPart(tool_name=tool_name, args={}))


class FakeRunResult:
    usage = RunUsage(input_tokens=1, output_tokens=2, requests=1)

    def __init__(self, output: str) -> None:
        self.output = output

    def all_messages(self) -> list[ModelRequest | ModelResponse]:
        return [ModelResponse(parts=[TextPart(content=self.output)])]


class FakeEventAgent:
    """Replays a scripted event stream through ``run_stream_events``."""

    def __init__(self, events: list[Any], final_output: str, raises: Exception | None = None) -> None:
        self.model = TestModel()
        self.events = events
        self.final_output = final_output
        self.raises = raises

    @asynccontextmanager
    async def run_stream_events(self, **_kwargs: Any) -> AsyncGenerator[AsyncIterable[Any]]:
        async def stream() -> AsyncIterable[Any]:
            for event in self.events:
                yield event
            if self.raises is not None:
                raise self.raises
            yield AgentRunResultEvent(cast(Any, FakeRunResult(self.final_output)))

        yield stream()


class FakeStreamResult:
    usage = RunUsage(input_tokens=1, output_tokens=2, requests=1)

    async def stream_text(self, delta: bool, debounce_by: float) -> AsyncIterable[str]:
        assert delta is True
        assert debounce_by == 0.2
        for chunk in ("hel", "lo"):
            yield chunk

    async def get_output(self) -> str:
        return "hello"

    def all_messages(self) -> list[ModelRequest | ModelResponse]:
        return []


class FakeStreamingAgent:
    def __init__(self) -> None:
        self.model = TestModel()

    @asynccontextmanager
    async def run_stream(self, **kwargs: Any) -> AsyncGenerator[FakeStreamResult]:
        event_handler = kwargs["event_stream_handler"]
        await event_handler(
            None,
            _tool_events("search", "search", "notes"),
        )
        yield FakeStreamResult()


async def _tool_events(*tool_names: str) -> AsyncIterable[FunctionToolCallEvent]:
    for tool_name in tool_names:
        yield tool_call(tool_name)


def test_build_model_settings_injects_openai_extra_body() -> None:
    model_settings = build_model_settings(
        {"temperature": 0, "extra_body": {"existing": "kept"}},
        AIRequestOptions(user_tracking_id="chat-iid", session_id="session-id", service_tier="flex"),
    )

    assert model_settings == {
        "temperature": 0,
        "extra_body": {
            "existing": "kept",
            "user": "chat-iid",
            "session_id": "session-id",
            "service_tier": "flex",
        },
    }


async def test_run_ai_text_wraps_output_usage_and_messages() -> None:
    agent = Agent(TestModel(custom_output_text="hello"), output_type=str)

    result = await run_ai_text(agent, "Say hello")

    assert result.output == "hello"
    assert result.usage.requests == 1
    assert result.message_history
    assert result.retries == 0


async def test_run_ai_structured_wraps_typed_output() -> None:
    agent = Agent(TestModel(custom_output_args={"value": "ok"}), output_type=StructuredOutput)

    result = await run_ai_structured(agent, "Return structured output")

    assert result.output == StructuredOutput(value="ok")
    assert result.usage.total_tokens


async def test_run_ai_stream_legacy_path_sends_cumulative_text_and_deduplicates_tools() -> None:
    streamed_text: list[str] = []
    tool_calls: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    async def on_tool_call(tool_name: str) -> None:
        tool_calls.append(tool_name)

    result = await run_ai_stream(
        cast(Agent[Any, str], FakeStreamingAgent()),
        user_prompt="Say hello",
        on_text_stream=on_text_stream,
        on_tool_call=on_tool_call,
        stream_options=ChatbotStreamOptions(continuation=False),
    )

    assert streamed_text == ["hel", "hello"]
    assert tool_calls == ["search", "notes"]
    assert result.output == "hello"


async def test_run_ai_stream_concatenates_text_from_every_round(no_stream_debounce: None) -> None:
    """A model that narrates, calls a tool, then answers must keep both rounds of text.

    `Agent.run_stream` ended the run at the narration and dropped the answer; this is the
    regression guard for that.
    """
    streamed_text: list[str] = []
    tool_calls: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    async def on_tool_call(tool_name: str) -> None:
        tool_calls.append(tool_name)

    agent = FakeEventAgent(
        events=[
            *text_round("Let me check ", "the docs."),
            tool_call("sophie_help"),
            tool_call("sophie_help"),
            *text_round("Antiflood ", "works like this."),
        ],
        final_output="Antiflood works like this.",
    )

    result = await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="How does antiflood work?",
        on_text_stream=on_text_stream,
        on_tool_call=on_tool_call,
    )

    assert result.output == "Let me check the docs.\n\nAntiflood works like this."
    assert tool_calls == ["sophie_help"]
    # Cumulative, never a bare delta, and the second round appends to the first.
    assert streamed_text[-1] == result.output
    assert streamed_text[1] == "Let me check "
    assert "Let me check the docs." in streamed_text


async def test_run_ai_stream_routes_thinking_away_from_the_answer(no_stream_debounce: None) -> None:
    streamed_text: list[str] = []
    streamed_reasoning: list[str] = []

    async def on_text_stream(text: str) -> None:
        streamed_text.append(text)

    async def on_reasoning_stream(text: str) -> None:
        streamed_reasoning.append(text)

    agent = FakeEventAgent(
        events=[*thinking_round("The user ", "wants antiflood."), *text_round("Antiflood works.")],
        final_output="Antiflood works.",
    )

    result = await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="How does antiflood work?",
        on_text_stream=on_text_stream,
        on_reasoning_stream=on_reasoning_stream,
    )

    assert result.output == "Antiflood works."
    assert streamed_reasoning[-1] == "The user wants antiflood."
    assert all("wants antiflood" not in text for text in streamed_text)


async def test_run_ai_stream_returns_partial_text_when_usage_limit_is_hit(no_stream_debounce: None) -> None:
    agent = FakeEventAgent(
        events=text_round("Partial answer"),
        final_output="",
        raises=UsageLimitExceeded("request limit exceeded"),
    )

    async def on_text_stream(_text: str) -> None:
        return None

    result = await run_ai_stream(
        cast(Agent[Any, str], agent),
        user_prompt="How does antiflood work?",
        on_text_stream=on_text_stream,
        stream_options=ChatbotStreamOptions(partial_on_limit=True),
    )

    assert result.truncated is True
    assert result.output == "Partial answer"


async def test_run_ai_stream_fails_on_usage_limit_without_partial_delivery(no_stream_debounce: None) -> None:
    agent = FakeEventAgent(
        events=text_round("Partial answer"),
        final_output="",
        raises=UsageLimitExceeded("request limit exceeded"),
    )

    async def on_text_stream(_text: str) -> None:
        return None

    with pytest.raises(AIRequestFailed):
        await run_ai_stream(
            cast(Agent[Any, str], agent),
            user_prompt="How does antiflood work?",
            on_text_stream=on_text_stream,
            stream_options=ChatbotStreamOptions(partial_on_limit=False),
        )


@pytest.fixture
def immediate_retries(monkeypatch: Any) -> Iterator[None]:
    """Run each attempt exactly once, so assertions see the candidate sequence and nothing else."""

    async def fake_retries(operation: Any, on_retry: Any = None) -> Any:
        return await operation()

    monkeypatch.setattr(ai_run, "run_ai_request_with_retries", fake_retries)
    yield


async def test_the_first_candidate_serves_when_it_succeeds(immediate_retries: None) -> None:
    primary = TestModel()
    seen_models: list[Any] = []

    async def operation(active_model: Any) -> str:
        seen_models.append(active_model)
        return "from-primary"

    result, served_model = await ai_run._run_with_model_candidates(operation, [primary], primary)

    assert result == "from-primary"
    assert served_model is primary
    # ``None`` keeps the agent on the model it was built with, so the common path adds no override.
    assert seen_models == [None]


async def test_a_failing_candidate_hands_over_to_the_next(immediate_retries: None) -> None:
    primary = TestModel()
    backup = TestModel()
    seen_models: list[Any] = []

    async def operation(active_model: Any) -> str:
        seen_models.append(active_model)
        if active_model is None:
            raise TimeoutError("primary provider is down")
        return "from-backup"

    result, served_model = await ai_run._run_with_model_candidates(operation, [primary, backup], primary)

    # The served model must be the one that answered, so callers attribute usage/metrics correctly.
    assert result == "from-backup"
    assert served_model is backup
    assert seen_models == [None, backup]


async def test_a_flat_provider_rejection_also_hands_over(immediate_retries: None) -> None:
    """The request that most needs another model — an image a model cannot read — is a plain 400."""
    primary = TestModel()
    backup = TestModel()

    async def operation(active_model: Any) -> str:
        if active_model is None:
            raise ModelHTTPError(status_code=400, model_name="primary", body="no image support")
        return "from-backup"

    result, served_model = await ai_run._run_with_model_candidates(operation, [primary, backup], primary)

    assert result == "from-backup"
    assert served_model is backup


async def test_a_usage_limit_stops_the_chain(immediate_retries: None) -> None:
    """Sophie's own budget ended the run; the next model would only spend more of it."""
    primary = TestModel()
    backup = TestModel()
    seen_models: list[Any] = []

    async def operation(active_model: Any) -> str:
        seen_models.append(active_model)
        raise UsageLimitExceeded("request limit exceeded")

    with pytest.raises(UsageLimitExceeded):
        await ai_run._run_with_model_candidates(operation, [primary, backup], primary)

    assert seen_models == [None]


async def test_the_last_candidates_error_is_raised(immediate_retries: None) -> None:
    """Exhaustion keeps error semantics: callers still see a provider error to map onto a message."""
    primary = TestModel()
    backup = TestModel()

    async def operation(active_model: Any) -> str:
        raise TimeoutError("everything is down")

    with pytest.raises(TimeoutError, match="everything is down"):
        await ai_run._run_with_model_candidates(operation, [primary, backup], primary)


async def test_an_empty_answer_hands_over_to_the_next_candidate(immediate_retries: None) -> None:
    primary = TestModel()
    backup = TestModel()

    async def operation(active_model: Any) -> str:
        return "" if active_model is None else "a real answer"

    result, served_model = await ai_run._run_with_model_candidates(
        operation, [primary, backup], primary, is_refusal=is_refusal_output
    )

    assert result == "a real answer"
    assert served_model is backup


async def test_the_last_candidates_empty_answer_is_returned(immediate_retries: None) -> None:
    """An empty answer is still an answer; turning the end of the chain into an error would not be."""
    primary = TestModel()

    async def operation(active_model: Any) -> str:
        return ""

    result, served_model = await ai_run._run_with_model_candidates(
        operation, [primary], primary, is_refusal=is_refusal_output
    )

    assert result == ""
    assert served_model is primary


def test_the_agents_own_model_leads_and_the_last_resort_closes(monkeypatch: Any) -> None:
    agent_model = TestModel()
    plan_model = TestModel()
    last_resort = TestModel()
    monkeypatch.setattr(ai_run, "_resolve_fallback_model", lambda model: last_resort)
    plan = AIModelPlan(candidates=(AIModelCandidate(model=plan_model, model_name="plan"),))

    candidates = ai_run.resolve_candidate_models(agent_model, plan, has_images=False)

    assert candidates == [agent_model, plan_model, last_resort]


def test_a_candidate_ruled_out_for_images_cannot_return_through_the_agent(monkeypatch: Any) -> None:
    text_only = TestModel()
    visual = TestModel()
    monkeypatch.setattr(ai_run, "_resolve_fallback_model", lambda model: None)
    plan = AIModelPlan(
        candidates=(
            AIModelCandidate(model=text_only, model_name="text-only", supports_images=False),
            AIModelCandidate(model=visual, model_name="visual"),
        )
    )

    # The agent was built with the plan's primary, which is exactly the model an image turn must skip.
    assert ai_run.resolve_candidate_models(text_only, plan, has_images=True) == [visual]
    assert ai_run.resolve_candidate_models(text_only, plan, has_images=False) == [text_only, visual]


def test_a_hand_picked_model_the_plan_never_had_still_leads(monkeypatch: Any) -> None:
    """Only a model the plan deliberately ruled out is dropped; a caller's own choice is not."""
    hand_picked = TestModel()
    visual = TestModel()
    monkeypatch.setattr(ai_run, "_resolve_fallback_model", lambda model: None)
    plan = AIModelPlan(candidates=(AIModelCandidate(model=visual, model_name="visual"),))

    assert ai_run.resolve_candidate_models(hand_picked, plan, has_images=True) == [hand_picked, visual]


def test_an_image_in_the_prompt_is_detected() -> None:
    png = BinaryContent(data=b"\x89PNG", media_type="image/png")

    assert request_has_images([png])
    assert request_has_images(["just text"]) is False
    assert request_has_images("just text") is False
    assert request_has_images(None) is False


def test_audio_is_not_an_image() -> None:
    """Audio reaches the model transcribed to text, which every candidate can read."""
    assert request_has_images([BinaryContent(data=b"RIFF", media_type="audio/wav")]) is False


def test_an_image_folded_into_the_history_is_detected() -> None:
    png = BinaryContent(data=b"\x89PNG", media_type="image/png")
    history = [ModelRequest(parts=[UserPromptPart(content=[png])])]

    assert request_has_images("follow-up question", history)


def test_a_plan_drops_a_model_that_already_appears_earlier() -> None:
    first = TestModel()
    duplicate = TestModel()
    plan = build_model_plan(
        [
            AIModelCandidate(model=first, model_name="same"),
            AIModelCandidate(model=duplicate, model_name="same"),
            AIModelCandidate(model=TestModel(), model_name="other"),
        ]
    )

    assert plan.model_names == ("same", "other")
    assert plan.primary is first


def test_an_empty_plan_cannot_serve_a_request() -> None:
    with pytest.raises(ValueError, match="no candidates"):
        _ = AIModelPlan().primary


def test_an_all_text_only_plan_still_tries_rather_than_refusing() -> None:
    """Better a model that may not see the image than no answer at all — the pre-filtering behaviour."""
    text_only = AIModelCandidate(model=TestModel(), model_name="text-only", supports_images=False)
    plan = AIModelPlan(candidates=(text_only,))

    assert plan.eligible(has_images=True) == (text_only,)
