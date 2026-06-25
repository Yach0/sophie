from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import asynccontextmanager
from typing import Any, cast

from pydantic import BaseModel
from pydantic_ai import Agent, FunctionToolCallEvent
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_run import (
    AIRequestOptions,
    build_model_settings,
    run_ai_stream,
    run_ai_structured,
    run_ai_text,
)


class StructuredOutput(BaseModel):
    value: str


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
        yield FunctionToolCallEvent(part=ToolCallPart(tool_name=tool_name, args={}))


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


async def test_run_ai_stream_sends_cumulative_text_and_deduplicates_tools() -> None:
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
    )

    assert streamed_text == ["hel", "hello"]
    assert tool_calls == ["search", "notes"]
    assert result.output == "hello"


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
