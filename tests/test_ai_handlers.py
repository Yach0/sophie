from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.handlers.op_prices import op_ai_prices_handler
from sophie_bot.modules.ai.handlers.op_stats import _build_doc
from sophie_bot.modules.ai.handlers.usage import AiUsage
from sophie_bot.modules.ai.utils.ai_catalog import AICatalog, CatalogModel, CatalogProvider
from sophie_bot.modules.ai.utils.ai_credit_text import format_credit_amount
from sophie_bot.modules.ai.utils.ai_header import ai_credit_header
from sophie_bot.modules.ai.utils.ai_quota_docs import build_chatbot_quota_exhausted_doc
from sophie_bot.modules.ai.utils.ai_usage_service import (
    ChatUsageBreakdownItem,
    ChatUsageView,
    OperatorAIStats,
    OperatorFeatureStats,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT


def _build_ai_usage_handler(
    test_services: ApplicationServices,
) -> tuple[AiUsage, SimpleNamespace]:
    event = SimpleNamespace(
        bot=test_services.bot,
        chat=SimpleNamespace(id=-1001),
        message_id=42,
        message_thread_id=None,
        reply=AsyncMock(),
    )
    handler = object.__new__(AiUsage)
    handler.event = event
    handler.data = {
        "context": SimpleNamespace(connection=SimpleNamespace(db_model=SimpleNamespace(iid="chat_iid"))),
        "services": test_services,
    }
    return handler, event


@pytest.mark.asyncio
async def test_aiusage_shows_credit_breakdown(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: ApplicationServices
) -> None:
    handler, event = _build_ai_usage_handler(test_services)
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

    text = event.bot.send_rich_message.await_args.kwargs["rich_message"].html
    assert text.count('<tg-emoji emoji-id="5816915599019741395">🔋</tg-emoji>') == 4
    assert "🔋</tg-emoji> <code>55</code> out of" in text
    assert "🔋</tg-emoji> <code>19,945</code> (99%)" in text
    assert "🤖" in text
    assert "🔋</tg-emoji> <code>22</code>" in text
    assert "🥡" not in text
    assert "<code>100</code>%" in text


@pytest.mark.asyncio
async def test_aiusage_shows_exhausted_state(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: ApplicationServices
) -> None:
    handler, event = _build_ai_usage_handler(test_services)
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

    text = event.bot.send_rich_message.await_args.kwargs["rich_message"].html
    assert "Quota exhausted!" in text
    assert "Mar 31, 2026" in text or "March 31, 2026" in text


def test_ai_credit_header_matches_usage_percentage() -> None:
    header = ai_credit_header(99)
    assert "99%" in header.to_html()


def test_format_credit_amount_uses_battery_custom_emoji() -> None:
    rendered = format_credit_amount(1234).to_rich()

    assert rendered == '<tg-emoji emoji-id="5816915599019741395">🔋</tg-emoji> <code>1,234</code>'
    assert "🥡" not in rendered


def test_operator_stats_uses_battery_custom_emoji_for_all_credit_values() -> None:
    rendered = _build_doc(
        OperatorAIStats(
            total_requests_today=1,
            total_requests_week=2,
            total_requests_month=3,
            total_credits_month=4,
            top_chats_by_requests=(),
            top_chats_by_credits=(),
            top_users_by_requests=(),
            top_users_by_credits=(),
            top_features=(
                OperatorFeatureStats(
                    feature=AI_FEATURE_CHATBOT,
                    title="Chatbot",
                    icon="🤖",
                    requests=1,
                    credits=5,
                ),
            ),
        )
    ).to_rich()

    assert rendered.count('<tg-emoji emoji-id="5816915599019741395">🔋</tg-emoji>') == 2
    assert "🥡" not in rendered


def test_quota_exhausted_message_uses_battery_custom_emoji_for_total() -> None:
    rendered = build_chatbot_quota_exhausted_doc(20000, date(2026, 3, 31)).to_rich()

    assert '<tg-emoji emoji-id="5816915599019741395">🔋</tg-emoji> <code>20,000</code>' in rendered
    assert "🥡" not in rendered


@pytest.mark.asyncio
async def test_op_aiprices_lists_model_prices(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: ApplicationServices
) -> None:
    message = SimpleNamespace(reply=AsyncMock())
    monkeypatch.setattr(
        "sophie_bot.modules.ai.handlers.op_prices.get_model_pricing",
        AsyncMock(return_value=(0.15, 0.60)),
    )
    provider = CatalogProvider(name="openrouter", kind="openrouter", base_url=None, api_key="k")
    catalog = AICatalog(
        version="1",
        providers={provider.name: provider},
        models={
            "some/model": CatalogModel(
                name="some/model", provider=provider, api_name="some/model", supports_reasoning=True, extra_params=None
            )
        },
        roles={(AIMode.entertainment, AIModelPurpose.chatbot): "some/model"},
    )
    monkeypatch.setattr("sophie_bot.modules.ai.handlers.op_prices.get_catalog", AsyncMock(return_value=catalog))

    await op_ai_prices_handler(message, services=test_services)

    text = message.reply.await_args.args[0]
    assert "AI Prices" in text
    assert "$0.15/1M" in text
    assert "$0.60/1M" in text
    assert "some/model" in text
    assert "entertainment:chatbot" in text
