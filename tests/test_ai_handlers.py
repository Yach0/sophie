from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules.ai.handlers.op_prices import op_ai_prices_handler
from sophie_bot.modules.ai.handlers.usage import AiUsage
from sophie_bot.modules.ai.utils.ai_header import ai_credit_header
from sophie_bot.modules.ai.utils.ai_usage_service import ChatUsageBreakdownItem, ChatUsageView
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT


def _build_ai_usage_handler() -> tuple[AiUsage, SimpleNamespace]:
    event = SimpleNamespace(reply=AsyncMock())
    handler = object.__new__(AiUsage)
    handler.event = event
    handler.data = {
        "connection": SimpleNamespace(db_model=SimpleNamespace(iid="chat_iid")),
    }
    return handler, event


@pytest.mark.asyncio
async def test_aiusage_shows_credit_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, event = _build_ai_usage_handler()
    monkeypatch.setattr(
        "sophie_bot.modules.ai.handlers.usage.get_chat_usage_view",
        AsyncMock(
            return_value=ChatUsageView(
                total_credits=20000,
                used_credits=55,
                remaining_credits=19945,
                percentage_remaining=99,
                period_end=date(2026, 3, 31),
                breakdown=(
                    ChatUsageBreakdownItem(
                        feature=AI_FEATURE_CHATBOT,
                        title="Chatbot",
                        icon="🤖",
                        credits=22,
                        percentage=100,
                    ),
                ),
            )
        ),
    )

    await AiUsage.handle(handler)

    text = event.reply.await_args.args[0]
    assert "🥡 55 out of 🥡 20,000" in text
    assert "🥡 19,945 (99%)" in text
    assert "🤖" in text
    assert "🥡 <code>22</code>" in text
    assert "<code>100</code>%" in text


@pytest.mark.asyncio
async def test_aiusage_shows_exhausted_state(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, event = _build_ai_usage_handler()
    monkeypatch.setattr(
        "sophie_bot.modules.ai.handlers.usage.get_chat_usage_view",
        AsyncMock(
            return_value=ChatUsageView(
                total_credits=100,
                used_credits=100,
                remaining_credits=0,
                percentage_remaining=0,
                period_end=date(2026, 3, 31),
                breakdown=(),
            )
        ),
    )

    await AiUsage.handle(handler)

    text = event.reply.await_args.args[0]
    assert "Quota exhausted!" in text
    assert "Mar 31, 2026" in text or "March 31, 2026" in text


def test_ai_credit_header_matches_usage_percentage() -> None:
    header = ai_credit_header(99)
    assert "Quota 99%" in header.to_html()


@pytest.mark.asyncio
async def test_op_aiprices_lists_model_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(reply=AsyncMock())
    monkeypatch.setattr(
        "sophie_bot.modules.ai.handlers.op_prices.get_model_pricing",
        AsyncMock(return_value=(0.15, 0.60)),
    )

    await op_ai_prices_handler(message)

    text = message.reply.await_args.args[0]
    assert "AI Prices" in text
    assert "$0.15/1M" in text
    assert "$0.60/1M" in text
    assert "default chat" in text
