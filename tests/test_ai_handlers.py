from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.ai.ai_quota import AIQuotaModel
from sophie_bot.modules.ai.handlers.op_prices import op_ai_prices_handler
from sophie_bot.modules.ai.handlers.usage import AiUsage
from sophie_bot.modules.ai.utils.ai_catalog import AICatalog, CatalogModel, CatalogProvider
from sophie_bot.modules.ai.utils.ai_header import ai_credit_header
from sophie_bot.modules.ai.utils.ai_quota import AIQuotaState, get_period_end, get_quota_info
from sophie_bot.modules.ai.utils.ai_quota_docs import (
    build_chatbot_quota_exhausted_doc,
    build_feature_quota_exhausted_doc,
)
from sophie_bot.modules.ai.utils.ai_usage_service import ChatUsageBreakdownItem, ChatUsageView
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT


def _build_ai_usage_handler(
    test_services: object,
) -> tuple[AiUsage, SimpleNamespace]:
    event = SimpleNamespace(reply=AsyncMock())
    handler = object.__new__(AiUsage)
    handler.event = event
    handler.data = {
        "context": SimpleNamespace(
            connection=SimpleNamespace(
                db_model=SimpleNamespace(iid="chat_iid")
            )
        ),
        "services": test_services,
    }
    return handler, event


@pytest.mark.asyncio
async def test_aiusage_shows_credit_breakdown(monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object) -> None:
    handler, event = _build_ai_usage_handler(test_services)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.handlers.usage.get_chat_usage_view",
        AsyncMock(
            return_value=ChatUsageView(
                total_credits=20000,
                used_credits=55,
                remaining_credits=19945,
                percentage_remaining=99,
                period_end=date(2026, 4, 1),
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
@pytest.mark.parametrize(
    ("period_start", "reset_date", "display_date"),
    [
        (date(2026, 4, 1), date(2026, 5, 1), "May 01, 2026"),
        (date(2026, 12, 1), date(2027, 1, 1), "January 01, 2027"),
        (date(2024, 2, 1), date(2024, 3, 1), "March 01, 2024"),
    ],
)
async def test_quota_reset_display_is_next_month_boundary(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    period_start: date,
    reset_date: date,
    display_date: str,
) -> None:
    handler, event = _build_ai_usage_handler(test_services)
    state = AIQuotaState(
        quota=AIQuotaModel.model_construct(period_start=period_start, monthly_credits=100, used_credits=100),
        usage=None,
        month_key=period_start.strftime("%Y-%m"),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_quota.get_quota_state", AsyncMock(return_value=state))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_usage_service.get_quota_state", AsyncMock(return_value=state))

    quota_info = await get_quota_info(handler.connection.db_model.iid, redis=test_services.redis)
    assert quota_info is not None
    chatbot_text = str(build_chatbot_quota_exhausted_doc(quota_info.total_credits, quota_info.period_end))
    feature_text = str(build_feature_quota_exhausted_doc(quota_info.period_end))
    await AiUsage.handle(handler)
    usage_text = event.reply.await_args.args[0]

    assert f"Quota resets on <code>{display_date}</code>." in chatbot_text
    assert f"AI features are disabled till <code>{display_date}</code>." in feature_text
    assert f"<b>Resets</b>: <i>{display_date}</i>" in usage_text
    assert f"Quota exhausted! AI features are disabled until <i>{display_date}</i>." in usage_text
    assert quota_info.period_end == get_period_end(period_start) == reset_date


def test_ai_credit_header_matches_usage_percentage() -> None:
    header = ai_credit_header(99)
    assert "99%" in header.to_html()


@pytest.mark.asyncio
async def test_op_aiprices_lists_model_prices(monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object) -> None:
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
