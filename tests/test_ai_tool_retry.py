from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Literal

import pytest
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from tenacity import wait_none

from sophie_bot.modules.ai.utils import ai_errors, ai_run
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_refusal import AIModelRefused
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult

Runner = Literal["text", "stream", "structured"]


@pytest.fixture(autouse=True)
def immediate_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the real retry policy, without waiting between attempts or calling a provider.
    monkeypatch.setattr(ai_errors, "AI_REQUEST_RETRY_WAIT", wait_none())
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)


def scripted_model(name: str, respond: Callable[[list[ModelMessage]], ModelResponse]) -> FunctionModel:
    async def request(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return respond(messages)

    async def stream(messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        response = respond(messages)
        for index, part in enumerate(response.parts):
            if isinstance(part, ToolCallPart):
                yield {index: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str())}
            elif isinstance(part, TextPart):
                yield part.content

    return FunctionModel(request, stream_function=stream, model_name=name)


def tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def mutate_or_answer(messages: list[ModelMessage]) -> ModelResponse:
    if tool_returns(messages):
        return ModelResponse(parts=[TextPart("done")])
    return ModelResponse(parts=[ToolCallPart("mutate", {"amount": 1})])


async def run_agent(
    runner: Runner,
    agent: Agent[None, str],
    plan: AIModelPlan | None = None,
) -> AIAgentResult[str]:
    async def on_text_stream(text: str) -> None:
        pass

    if runner == "stream":
        return await ai_run.run_ai_stream(agent, "make the change", on_text_stream, model_plan=plan)
    if runner == "structured":
        return await ai_run.run_ai_structured(agent, "make the change", model_plan=plan)
    return await ai_run.run_ai_text(agent, "make the change", model_plan=plan)


@pytest.mark.parametrize("runner", ["stream", "structured"])
async def test_forwarded_capabilities_none_preserves_tool_execution(runner: Runner) -> None:
    mutations: list[int] = []
    agent = Agent(scripted_model("primary", mutate_or_answer))

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    async def on_text_stream(text: str) -> None:
        pass

    if runner == "stream":
        result = await ai_run.run_ai_stream(agent, "make the change", on_text_stream, capabilities=None)
    else:
        result = await ai_run.run_ai_structured(agent, "make the change", capabilities=None)

    assert result.output == "done"
    assert mutations == [1]
    assert [part.content for part in tool_returns(result.new_messages)] == ["mutation completed"]


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
@pytest.mark.parametrize("status_code", [400, 503])
async def test_provider_failure_after_mutation_does_not_restart(runner: Runner, status_code: int) -> None:
    mutations: list[int] = []
    attempted: list[str] = []
    error = ModelHTTPError(status_code, "primary", body="provider failed after the tool returned")

    def primary_response(messages: list[ModelMessage]) -> ModelResponse:
        attempted.append("primary")
        if tool_returns(messages):
            raise error
        return mutate_or_answer(messages)

    def backup_response(messages: list[ModelMessage]) -> ModelResponse:
        attempted.append("backup")
        return mutate_or_answer(messages)

    primary = scripted_model("primary", primary_response)
    backup = scripted_model("backup", backup_response)
    plan = AIModelPlan(candidates=(AIModelCandidate(primary, "primary"), AIModelCandidate(backup, "backup")))
    agent = Agent(primary)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    with capture_run_messages() as messages, pytest.raises(AIRequestFailed) as raised:
        await run_agent(runner, agent, plan)

    assert raised.value.__cause__ is error
    assert mutations == [1]
    assert attempted == ["primary", "primary"]
    assert [part.content for part in tool_returns(messages)] == ["mutation completed"]


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
async def test_last_resort_cannot_restart_after_mutation(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    mutations: list[int] = []
    backup_calls: list[bool] = []

    def primary_response(messages: list[ModelMessage]) -> ModelResponse:
        if tool_returns(messages):
            raise ModelHTTPError(503, "primary", body="unavailable")
        return mutate_or_answer(messages)

    def backup_response(messages: list[ModelMessage]) -> ModelResponse:
        backup_calls.append(True)
        return mutate_or_answer(messages)

    backup = scripted_model("backup", backup_response)
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: AIModelCandidate(backup, "backup"))
    agent = Agent(scripted_model("primary", primary_response))

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    with pytest.raises(AIRequestFailed):
        await run_agent(runner, agent)

    assert mutations == [1]
    assert backup_calls == []


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
@pytest.mark.parametrize("recovery", ["retry", "fallback"])
async def test_provider_failure_before_any_tool_still_recovers(
    runner: Runner, recovery: Literal["retry", "fallback"]
) -> None:
    attempted: list[str] = []
    mutations: list[int] = []

    def primary_response(messages: list[ModelMessage]) -> ModelResponse:
        attempted.append("primary")
        if recovery == "fallback" or len(attempted) == 1:
            raise ModelHTTPError(400 if recovery == "fallback" else 503, "primary", body="unavailable")
        return mutate_or_answer(messages)

    def backup_response(messages: list[ModelMessage]) -> ModelResponse:
        attempted.append("backup")
        return mutate_or_answer(messages)

    primary = scripted_model("primary", primary_response)
    backup = scripted_model("backup", backup_response)
    plan = AIModelPlan(candidates=(AIModelCandidate(primary, "primary"), AIModelCandidate(backup, "backup")))
    agent = Agent(primary)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    result = await run_agent(runner, agent, plan)

    assert result.output == "done"
    assert mutations == [1]
    assert attempted == ["primary", *(["backup"] * 2 if recovery == "fallback" else ["primary"] * 2)]
    assert result.served_model is (backup if recovery == "fallback" else primary)


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
async def test_native_request_fallback_preserves_completed_tool_return(runner: Runner) -> None:
    mutations: list[int] = []
    backup_returns: list[ToolReturnPart] = []

    def primary_response(messages: list[ModelMessage]) -> ModelResponse:
        if tool_returns(messages):
            raise ModelHTTPError(503, "primary", body="unavailable")
        return mutate_or_answer(messages)

    def backup_response(messages: list[ModelMessage]) -> ModelResponse:
        backup_returns.extend(tool_returns(messages))
        return mutate_or_answer(messages)

    model = FallbackModel(scripted_model("primary", primary_response), scripted_model("backup", backup_response))
    agent = Agent(model)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    result = await run_agent(runner, agent)

    assert result.output == "done"
    assert mutations == [1]
    assert [part.content for part in backup_returns] == ["mutation completed"]
    assert tool_returns(result.new_messages) == backup_returns


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
async def test_validation_without_tool_execution_does_not_block_retry(runner: Runner) -> None:
    calls = 0
    mutations: list[int] = []

    def respond(messages: list[ModelMessage]) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("mutate", {"amount": "invalid"})])
        if calls == 2:
            raise ModelHTTPError(503, "primary", body="unavailable")
        return mutate_or_answer(messages)

    agent = Agent(scripted_model("primary", respond))

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    result = await run_agent(runner, agent)

    assert result.output == "done"
    assert mutations == [1]
    assert calls == 4


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
@pytest.mark.parametrize("raised_refusal", [False, True])
async def test_refusal_after_mutation_cannot_restart(runner: Runner, raised_refusal: bool) -> None:
    mutations: list[int] = []

    def respond(messages: list[ModelMessage]) -> ModelResponse:
        if tool_returns(messages):
            return ModelResponse(parts=[TextPart(" ")])
        return mutate_or_answer(messages)

    primary = scripted_model("primary", respond)
    backup = scripted_model("backup", mutate_or_answer)
    plan = AIModelPlan(candidates=(AIModelCandidate(primary, "primary"), AIModelCandidate(backup, "backup")))
    agent = Agent(primary)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    @agent.output_validator
    async def validate(output: str) -> str:
        if raised_refusal:
            raise AIModelRefused("primary")
        return output

    if raised_refusal:
        with pytest.raises(AIModelRefused):
            await run_agent(runner, agent, plan)
    else:
        result = await run_agent(runner, agent, plan)
        assert result.output == " "
        assert [part.content for part in tool_returns(result.new_messages)] == ["mutation completed"]
    assert mutations == [1]


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
async def test_cancellation_after_mutation_propagates_without_retry(runner: Runner) -> None:
    mutations: list[int] = []

    def respond(messages: list[ModelMessage]) -> ModelResponse:
        if tool_returns(messages):
            raise asyncio.CancelledError
        return mutate_or_answer(messages)

    agent = Agent(scripted_model("primary", respond))

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    with capture_run_messages() as messages, pytest.raises(asyncio.CancelledError):
        await run_agent(runner, agent)

    assert mutations == [1]
    assert [part.content for part in tool_returns(messages)] == ["mutation completed"]


@pytest.mark.parametrize("runner", ["text", "stream", "structured"])
async def test_tool_failure_after_mutation_does_not_restart(runner: Runner) -> None:
    mutations: list[int] = []
    primary = scripted_model("primary", mutate_or_answer)
    agent = Agent(primary)
    backup = scripted_model("backup", mutate_or_answer)
    plan = AIModelPlan(candidates=(AIModelCandidate(primary, "primary"), AIModelCandidate(backup, "backup")))

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        raise TimeoutError("mutation completed but tool did not return")

    with pytest.raises(AIRequestFailed):
        await run_agent(runner, agent, plan)

    assert mutations == [1]


async def test_mid_stream_failure_after_mutation_does_not_restart() -> None:
    mutations: list[int] = []
    updates: list[str] = []
    requests = 0
    primary = scripted_model("primary", mutate_or_answer)
    initial_stream = primary.stream_function
    assert initial_stream is not None

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal requests
        requests += 1
        if tool_returns(messages):
            yield "the change was made"
            raise ModelHTTPError(503, "primary", body="stream interrupted")
        async for chunk in initial_stream(messages, info):
            yield chunk

    primary.stream_function = stream
    backup = scripted_model("backup", mutate_or_answer)
    plan = AIModelPlan(candidates=(AIModelCandidate(primary, "primary"), AIModelCandidate(backup, "backup")))
    agent = Agent(primary)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "mutation completed"

    async def on_text_stream(text: str) -> None:
        updates.append(text)

    with capture_run_messages() as messages, pytest.raises(AIRequestFailed):
        await ai_run.run_ai_stream(agent, "make the change", on_text_stream, model_plan=plan)

    assert updates == ["the change was made"]
    assert mutations == [1]
    assert requests == 2
    assert [part.content for part in tool_returns(messages)] == ["mutation completed"]
