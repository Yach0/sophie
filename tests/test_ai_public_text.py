from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, DeltaToolCall, FunctionModel
from tenacity import wait_none

from sophie_bot.modules.ai.utils import ai_errors
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, build_model_plan
from sophie_bot.modules.ai.utils.ai_run import run_ai_stream, run_ai_structured, run_ai_text

PUBLIC_ROUNDS = ("Before lookup.\n", "Between lookups.\n", "Final answer.")
PUBLIC_ANSWER = "".join(PUBLIC_ROUNDS)


def public_text_model(
    *, tool_rounds: int = 2, rounds: tuple[str, ...] = PUBLIC_ROUNDS, model_name: str = "public-text-test"
) -> FunctionModel:
    def response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        round_index = sum(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
        parts = [ThinkingPart("PRIVATE reasoning"), TextPart(rounds[round_index])]
        if round_index < tool_rounds:
            return ModelResponse(parts=[*parts, ToolCallPart("lookup", {}, f"lookup-{round_index}")])
        return ModelResponse(parts=parts)

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall] | dict[int, DeltaThinkingPart]]:
        for part_index, part in enumerate(response(messages, info).parts):
            if isinstance(part, ThinkingPart):
                yield {part_index: DeltaThinkingPart(content=part.content)}
            elif isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    part_index: DeltaToolCall(
                        name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id
                    )
                }

    return FunctionModel(response, stream_function=stream, model_name=model_name)


async def lookup() -> str:
    return "PRIVATE tool result"


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("tool_rounds", [0, 2])
async def test_public_text_survives_native_tool_rounds(streaming: bool, tool_rounds: int) -> None:
    agent = Agent[None, str](public_text_model(tool_rounds=tool_rounds), tools=[Tool[None](lookup)])
    updates: list[str] = []
    before_tools: list[str] = []

    async def on_text(text: str) -> None:
        updates.append(text)

    async def before_tool(_name: str) -> None:
        before_tools.append(updates[-1])

    # Prior turns must not contribute to this turn's public answer.
    history: list[ModelMessage] = [ModelResponse(parts=[TextPart("OLD answer")])]
    if streaming:
        result = await run_ai_stream(
            agent, "Look up twice", on_text, message_history=history, on_before_tool_call=before_tool
        )
    else:
        result = await run_ai_text(agent, "Look up twice", message_history=history)

    expected = "".join(PUBLIC_ROUNDS[: tool_rounds + 1])
    assert result.output == expected
    assert result.usage.requests == tool_rounds + 1
    assert (
        "".join(
            part.content
            for message in result.new_messages
            if isinstance(message, ModelResponse)
            for part in message.parts
            if isinstance(part, TextPart)
        )
        == expected
    )
    if streaming:
        assert updates[-1] == expected
        assert all(expected.startswith(update) for update in updates)
        assert before_tools == ["".join(PUBLIC_ROUNDS[: round_index + 1]) for round_index in range(tool_rounds)]


@pytest.mark.parametrize("streaming", [True, False])
async def test_identical_public_rounds_are_not_deduplicated(streaming: bool) -> None:
    agent = Agent[None, str](public_text_model(rounds=("Again. ",) * 3), tools=[Tool[None](lookup)])
    updates: list[str] = []

    async def on_text(text: str) -> None:
        updates.append(text)

    result = await run_ai_stream(agent, "Repeat", on_text) if streaming else await run_ai_text(agent, "Repeat")
    assert result.output == "Again. Again. Again. "
    if streaming:
        assert updates[-1] == result.output


async def test_empty_response_retry_preserves_identical_public_responses() -> None:
    async def response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if any(isinstance(part, RetryPromptPart) for message in messages for part in message.parts):
            return ModelResponse(parts=[TextPart("Again. ")])
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            return ModelResponse(parts=[])
        return ModelResponse(parts=[TextPart("Again. "), ToolCallPart("lookup", {})])

    agent = Agent[None, str](FunctionModel(response), tools=[Tool[None](lookup)])
    result = await run_ai_text(agent, "Look up")

    assert result.output == "Again. Again. "
    assert result.usage.requests == 3


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("retry_same_model", [True, False])
async def test_failed_attempt_public_text_does_not_leak(
    streaming: bool, retry_same_model: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = False
    success = public_text_model()

    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal failed
        if failed:
            assert success.function is not None
            successful_response = success.function(messages, info)
            assert isinstance(successful_response, ModelResponse)
            return successful_response
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            failed = True
            raise ModelHTTPError(503 if retry_same_model else 400, "failing-model", "Failed after tool")
        return ModelResponse(parts=[TextPart("FAILED candidate text"), ToolCallPart("lookup", {}, "failed-call")])

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        for part in response(messages, info).parts:
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {0: DeltaToolCall(name=part.tool_name, json_args="{}", tool_call_id=part.tool_call_id)}

    primary = FunctionModel(response, stream_function=stream, model_name="failing-model")
    plan = build_model_plan(
        [
            AIModelCandidate(model=primary, model_name=primary.model_name),
            AIModelCandidate(model=success, model_name=success.model_name),
        ]
    )
    monkeypatch.setattr(ai_errors, "AI_REQUEST_RETRY_WAIT", wait_none())
    agent = Agent[None, str](primary, tools=[Tool[None](lookup)])
    updates: list[str] = []

    async def on_text(text: str) -> None:
        updates.append(text)

    result = (
        await run_ai_stream(agent, "Look up", on_text, model_plan=plan)
        if streaming
        else await run_ai_text(agent, "Look up", model_plan=plan)
    )
    assert failed
    assert result.output == PUBLIC_ANSWER
    assert result.served_model is (primary if retry_same_model else success)
    assert "FAILED" not in str(result.new_messages)
    assert "FAILED" not in str(result.message_history)
    if streaming:
        assert "FAILED candidate text" in updates
        successful_updates = updates[updates.index("FAILED candidate text") + 1 :]
        assert successful_updates
        assert all("FAILED" not in update for update in successful_updates)
        assert updates[-1] == PUBLIC_ANSWER


async def test_string_classification_keeps_terminal_output() -> None:
    agent = Agent[None, str](public_text_model(), tools=[Tool[None](lookup)])
    result = await run_ai_structured(agent, "Classify")
    assert result.output == PUBLIC_ROUNDS[-1]


class Classification(BaseModel):
    label: str


async def test_structured_classification_keeps_validated_output() -> None:
    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            assert info.output_tools
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"label": "safe"})])
        return ModelResponse(parts=[TextPart("Public preamble"), ToolCallPart("lookup", {})])

    agent = Agent[None, Classification](FunctionModel(response), tools=[Tool[None](lookup)], output_type=Classification)
    result = await run_ai_structured(agent, "Classify")
    assert result.output == Classification(label="safe")


@pytest.mark.parametrize("streaming", [True, False])
async def test_empty_terminal_output_still_fails_over(streaming: bool) -> None:
    primary = public_text_model(model_name="empty-terminal")
    backup = public_text_model()
    plan = build_model_plan(
        [
            AIModelCandidate(model=primary, model_name=primary.model_name),
            AIModelCandidate(model=backup, model_name=backup.model_name),
        ]
    )
    agent = Agent[None, str](primary, tools=[Tool[None](lookup)])

    @agent.output_validator
    def empty_primary_output(ctx: RunContext[None], output: str) -> str:
        return "" if ctx.model is primary else output

    async def on_text(_text: str) -> None:
        return None

    result = (
        await run_ai_stream(agent, "Look up", on_text, model_plan=plan)
        if streaming
        else await run_ai_text(agent, "Look up", model_plan=plan)
    )
    assert result.served_model is backup
    assert result.output == PUBLIC_ANSWER


@pytest.mark.parametrize("streaming", [True, False])
async def test_cancellation_after_public_text_does_not_retry_or_fail_over(streaming: bool) -> None:
    model = public_text_model()
    calls = 0

    async def lookup() -> str:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    agent = Agent[None, str](model, tools=[Tool[None](lookup)])
    plan = build_model_plan([AIModelCandidate(model=model, model_name=model.model_name)])
    updates: list[str] = []

    async def on_text(text: str) -> None:
        updates.append(text)

    with pytest.raises(asyncio.CancelledError):
        if streaming:
            await run_ai_stream(agent, "Look up", on_text, model_plan=plan)
        else:
            await run_ai_text(agent, "Look up", model_plan=plan)
    assert calls == 1
    if streaming:
        assert updates == [PUBLIC_ROUNDS[0]]


async def test_completed_history_supplies_public_text_without_stream_events(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = Agent[None, str](public_text_model(), tools=[Tool[None](lookup)])
    native_result = await agent.run("Look up")
    assert native_result.output == PUBLIC_ROUNDS[-1]
    monkeypatch.setattr(agent, "run", AsyncMock(return_value=native_result))
    updates: list[str] = []

    async def on_text(text: str) -> None:
        updates.append(text)

    result = await run_ai_stream(agent, "Look up", on_text)
    assert result.output == PUBLIC_ANSWER
    assert updates == [PUBLIC_ANSWER]
