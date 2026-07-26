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
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
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


async def test_run_with_model_fallback_returns_primary_when_it_succeeds(monkeypatch: Any) -> None:
    primary = TestModel()

    async def fake_retries(operation: Any, on_retry: Any = None) -> Any:
        return await operation()

    monkeypatch.setattr(ai_run, "run_ai_request_with_retries", fake_retries)

    seen_models: list[Any] = []

    async def operation(active_model: Any) -> str:
        seen_models.append(active_model)
        return "from-primary"

    result, served_model = await ai_run._run_with_model_fallback(operation, primary)

    assert result == "from-primary"
    assert served_model is primary
    assert seen_models == [None]


async def test_run_with_model_fallback_returns_fallback_model_when_primary_fails(monkeypatch: Any) -> None:
    primary = TestModel()
    fallback = TestModel()

    async def fake_retries(operation: Any, on_retry: Any = None) -> Any:
        return await operation()

    monkeypatch.setattr(ai_run, "run_ai_request_with_retries", fake_retries)
    monkeypatch.setattr(ai_run, "_resolve_fallback_model", lambda model: fallback)

    seen_models: list[Any] = []

    async def operation(active_model: Any) -> str:
        seen_models.append(active_model)
        if active_model is None:
            raise TimeoutError("primary provider is down")
        return "from-fallback"

    result, served_model = await ai_run._run_with_model_fallback(operation, primary)

    # The served model must be the fallback so callers attribute usage/result metrics correctly.
    assert result == "from-fallback"
    assert served_model is fallback
    assert seen_models == [None, fallback]
