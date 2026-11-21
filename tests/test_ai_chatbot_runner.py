from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage, UsageLimits
from redis.exceptions import RedisError

from sophie_bot.modules.ai.utils import chatbot_agent
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions
from sophie_bot.modules.ai.utils.chatbot_agent import (
    ChatbotRunConfig,
    ChatbotRunRequest,
    run_chatbot,
)
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT


def _request() -> ChatbotRunRequest:
    context = SimpleNamespace(
        chat_iid="chat-iid",
        services=SimpleNamespace(redis=object()),
    )
    return ChatbotRunRequest(
        context=context,
        history=SimpleNamespace(prompt=["hello"], message_history=[]),
        model_plan=SimpleNamespace(primary=SimpleNamespace(model_name="model")),
    )


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    charge_side_effect: Exception | None = None,
) -> tuple[SimpleNamespace, AsyncMock]:
    result = SimpleNamespace(
        output="answer",
        usage=RunUsage(input_tokens=10, output_tokens=5),
        served_model=None,
    )
    monkeypatch.setattr(
        chatbot_agent,
        "_build_chatbot_run_config",
        AsyncMock(
            return_value=ChatbotRunConfig(
                agent=object(),
                usage_limits=object(),
                request_options=object(),
            )
        ),
    )
    monkeypatch.setattr(
        chatbot_agent,
        "run_ai_text",
        AsyncMock(return_value=result),
    )
    charge = AsyncMock(side_effect=charge_side_effect)
    monkeypatch.setattr(chatbot_agent, "charge_ai_usage", charge)
    return result, charge


@pytest.mark.asyncio
async def test_run_chatbot_charges_successful_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, charge = _patch_run(monkeypatch)

    result = await run_chatbot(_request())

    assert result is expected
    charge.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_chatbot_charge_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(
        monkeypatch,
        charge_side_effect=RedisError("unavailable"),
    )

    with pytest.raises(RedisError, match="unavailable"):
        await run_chatbot(_request())


async def test_request_budget_completion_charges_the_full_real_run(monkeypatch: pytest.MonkeyPatch) -> None:
    effects: list[int] = []
    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if (info.model_settings or {}).get("tool_choice") != "none":
            return ModelResponse(parts=[ToolCallPart("mutate", {"amount": calls}, tool_call_id=f"call-{calls}")])
        assert calls == 8
        assert len([part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]) == 7
        return ModelResponse(parts=[TextPart("Finished")])

    model = FunctionModel(respond)
    agent = Agent(model)

    @agent.tool_plain
    async def mutate(amount: int) -> str:
        effects.append(amount)
        return "completed"

    request = replace(_request(), model_plan=AIModelPlan((AIModelCandidate(model, model.model_name),)))
    monkeypatch.setattr(
        chatbot_agent,
        "_build_chatbot_run_config",
        AsyncMock(
            return_value=ChatbotRunConfig(agent, UsageLimits(request_limit=8), AIRequestOptions(single_model=True))
        ),
    )
    charge = AsyncMock()
    monkeypatch.setattr(chatbot_agent, "charge_ai_usage", charge)

    result = await run_chatbot(request)

    assert result.output == "Finished"
    assert effects == list(range(1, 8))
    assert result.usage.requests == calls == 8
    assert result.usage.tool_calls == 7
    assert result.usage.total_tokens > 0
    charge.assert_awaited_once_with(
        request.context.chat_iid, AI_FEATURE_CHATBOT, model, result.usage, redis=request.context.services.redis
    )
    assert (
        len([part for message in result.new_messages for part in message.parts if isinstance(part, ToolReturnPart)])
        == 7
    )
    assert isinstance(result.message_history[-1], ModelResponse)
