from __future__ import annotations

import json
from collections.abc import Sequence
from unittest.mock import MagicMock

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

from sophie_bot.modules.ai.utils import ai_run
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions
from sophie_bot.modules.ai.utils.ai_tokens import ModelTokenBudget, TokenCounter
from sophie_bot.modules.ai.utils.modern_context import ModernContext


@pytest.fixture(autouse=True)
def no_external_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)


def completion(model_name: str, message: dict[str, object], streaming: bool) -> httpx2.Response:
    payload = {
        "id": "offline",
        "created": 0,
        "model": model_name,
        "object": "chat.completion.chunk" if streaming else "chat.completion",
        "choices": [
            {
                "index": 0,
                "delta" if streaming else "message": message,
                "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
            }
        ],
    }
    if streaming:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n",
        )
    return httpx2.Response(200, json=payload)


@pytest.mark.parametrize("streaming", [False, True])
async def test_sdk_retries_only_the_failed_request_after_mutation(streaming: bool) -> None:
    payloads: list[dict[str, object]] = []
    mutations: list[int] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 2:
            return httpx2.Response(503, headers={"retry-after-ms": "1"}, json={"error": {"message": "unavailable"}})
        if len(payloads) == 1:
            call = {"id": "mutation", "type": "function", "function": {"name": "mutate", "arguments": '{"amount":1}'}}
            if streaming:
                call["index"] = 0
            return completion("offline", {"role": "assistant", "tool_calls": [call]}, streaming)
        assert any(
            message["role"] == "tool" and message["content"] == "mutation completed" for message in payload["messages"]
        )
        return completion("offline", {"role": "assistant", "content": "done"}, streaming)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = OpenAIChatModel("offline", provider=OpenAIProvider(api_key="offline", http_client=client))
        agent = Agent(model)

        @agent.tool_plain
        async def mutate(amount: int) -> str:
            mutations.append(amount)
            return "mutation completed"

        async def on_text(text: str) -> None:
            pass

        if streaming:
            result = await ai_run.run_ai_stream(agent, "make the change", on_text)
        else:
            result = await ai_run.run_ai_text(agent, "make the change")

    assert result.output == "done"
    assert mutations == [1]
    assert len(payloads) == 3
    assert payloads[1] == payloads[2]
    assert result.usage.requests == 2


async def test_each_candidate_gets_its_settings_and_its_own_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: list[dict[str, object]] = []
    reported: list[tuple[str, int]] = []
    monkeypatch.setattr(
        ai_run, "track_ai_usage", lambda model, usage: reported.append((model.model_name, usage.input_tokens))
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload["model"] == "primary":
            return httpx2.Response(400, json={"error": {"message": "unsupported"}})
        return completion("backup", {"role": "assistant", "content": "done"}, False)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = OpenAIProvider(api_key="offline", http_client=client)
        primary = OpenAIChatModel("primary", provider=provider, settings={"temperature": 0.1, "max_tokens": 31})
        backup = OpenAIChatModel("backup", provider=provider, settings={"temperature": 0.7, "max_tokens": 17})
        plan = AIModelPlan(
            (
                AIModelCandidate(primary, "primary", service_tier="flex"),
                AIModelCandidate(backup, "backup", service_tier="none"),
            )
        )
        result = await ai_run.run_ai_text(
            Agent(primary), "hello", model_plan=plan, request_options=AIRequestOptions(service_tier="default")
        )

    assert result.served_model is backup
    assert [(payload["temperature"], payload["max_completion_tokens"]) for payload in payloads] == [
        (0.1, 31),
        (0.7, 17),
    ]
    assert payloads[0]["service_tier"] == "flex"
    assert "service_tier" not in payloads[1]
    assert reported == [("backup", 0)]


async def test_context_projection_keeps_live_tool_results_on_fallback() -> None:
    projections: list[tuple[str, list[ModelRequest | ModelResponse]]] = []
    mutations: list[int] = []

    def project(
        candidate: AIModelCandidate, continuation: Sequence[ModelRequest | ModelResponse]
    ) -> tuple[list[UserContent], list[ModelRequest | ModelResponse]]:
        projections.append((candidate.model_name, list(continuation)))
        return ["current request"], [ModelRequest(parts=[UserPromptPart(candidate.model_name + " history")])]

    def primary_request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            raise ModelHTTPError(400, "primary", body="unsupported")
        return ModelResponse(parts=[ToolCallPart("mutate", {"amount": 1})], usage=RequestUsage(input_tokens=3))

    def backup_request(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert messages[0].parts[0].content == "backup history"
        assert any(
            isinstance(part, ToolReturnPart) and part.content == "completed"
            for message in messages
            for part in message.parts
        )
        return ModelResponse(parts=[TextPart("done")], usage=RequestUsage(input_tokens=5))

    primary = FunctionModel(primary_request, model_name="primary")
    backup = FunctionModel(backup_request, model_name="backup")
    agent = Agent(primary)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        mutations.append(amount)
        return "completed"

    plan = AIModelPlan((AIModelCandidate(primary, "primary"), AIModelCandidate(backup, "backup")))
    result = await ai_run.run_ai_text(
        agent, "current request", model_plan=plan, request_options=AIRequestOptions(context_projection=project)
    )
    assert result.output == "done"
    assert mutations == [1]
    assert result.usage.input_tokens == 8
    assert projections[-1][0] == "backup"
    assert len(projections[-1][1]) == 2


async def test_context_reserves_space_for_live_tool_messages() -> None:
    counter = TokenCounter("unknown")
    history = ModernContext(redis=MagicMock(), chat_tid=1)
    history.prompt = ["current"]
    budget = ModelTokenBudget(counter, 1000)
    history._token_budgets = (budget,)
    history._budget = budget.input_tokens
    candidate = AIModelCandidate(
        FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])), "unknown"
    )
    continuation = [ModelResponse(parts=[TextPart("tool output " * 2000)])]
    with pytest.raises(ValueError, match="exceeds its token budget"):
        history.project_for_candidate(candidate, continuation)
    assert history.prompt == ["current"]
    assert history._budget == 1000
