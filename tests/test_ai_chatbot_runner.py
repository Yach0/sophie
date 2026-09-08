from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from sophie_bot.modules.ai.utils import chatbot_agent
from sophie_bot.modules.ai.utils.chatbot_agent import (
    ChatbotRunConfig,
    ChatbotRunRequest,
    run_chatbot,
)


def _request(*, charge_failure_policy: str = "raise") -> ChatbotRunRequest:
    context = SimpleNamespace(
        chat_iid="chat-iid",
        services=SimpleNamespace(redis=object()),
    )
    return ChatbotRunRequest(
        context=context,
        history=SimpleNamespace(prompt=["hello"], message_history=[]),
        model_plan=SimpleNamespace(primary=SimpleNamespace(model_name="model")),
        charge_failure_policy=charge_failure_policy,
    )


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    *, charge_side_effect: Exception | None = None,
) -> tuple[SimpleNamespace, AsyncMock]:
    result = SimpleNamespace(
        output="answer",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
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
async def test_run_chatbot_best_effort_charge_policy_preserves_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected, charge = _patch_run(
        monkeypatch,
        charge_side_effect=RedisError("unavailable"),
    )

    result = await run_chatbot(_request(charge_failure_policy="best_effort"))

    assert result is expected
    charge.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_chatbot_strict_charge_policy_propagates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(
        monkeypatch,
        charge_side_effect=RedisError("unavailable"),
    )

    with pytest.raises(RedisError, match="unavailable"):
        await run_chatbot(_request())
