from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler

from sophie_bot.db.models import AIQuotaModel, AIUsageModel
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.utils.ai_quota import _ensure_period
from sophie_bot.modules.ai.utils.ai_model_pricing import estimate_model_credit_cost
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT, AI_FEATURE_TRANSLATE


async def _create_chat(chat_tid: int, title: str) -> ChatModel:
    chat = ChatModel(
        tid=chat_tid,
        type=ChatType.group,
        first_name_or_title=title,
        last_name=None,
        username=None,
        language_code=None,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    )
    await chat.save()
    return chat


@pytest.mark.asyncio
async def test_charge_ai_usage_weights_credits_by_model_price(db_init: object) -> None:
    cheap_chat = await _create_chat(-1001001, "Cheap")
    expensive_chat = await _create_chat(-1001002, "Expensive")
    usage = SimpleNamespace(total_tokens=200, request_tokens=100, response_tokens=100)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "sophie_bot.modules.ai.utils.ai_model_pricing.get_model_pricing",
            AsyncMock(side_effect=[(0.15, 0.60), (0.40, 4.00)]),
        )

        await charge_ai_usage(
            cheap_chat.iid, AI_FEATURE_CHATBOT, SimpleNamespace(model_name="mistralai/mistral-small-2603"), usage
        )
        await charge_ai_usage(
            expensive_chat.iid, AI_FEATURE_CHATBOT, SimpleNamespace(model_name="openai/gpt-5-mini"), usage
        )

    all_quotas = await AIQuotaModel.find_all().to_list()
    quotas_by_chat = {str(quota.chat.ref.id): quota for quota in all_quotas}
    cheap_quota = quotas_by_chat[str(cheap_chat.iid)]
    expensive_quota = quotas_by_chat[str(expensive_chat.iid)]
    assert cheap_quota.used_credits == 2
    assert expensive_quota.used_credits == 10


@pytest.mark.asyncio
async def test_estimate_model_credit_cost_uses_openrouter_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pricing_cache() -> dict[str, tuple[float | None, float | None]]:
        return {"custom/test-model": (0.30, 1.20)}

    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_model_pricing._load_openrouter_pricing_cache",
        fake_pricing_cache,
    )

    credits = await estimate_model_credit_cost("custom/test-model", 200, 100, 100)
    assert credits == 4


@pytest.mark.asyncio
async def test_checking_quota_resets_old_month_usage(db_init: object) -> None:
    chat = await _create_chat(-1001003, "Reset")
    quota = AIQuotaModel(
        chat=chat,
        period_start=(date.today().replace(day=1) - timedelta(days=31)).replace(day=1),
        used_credits=123,
        exhausted_notified_period_start=date.today().replace(day=1),
        exhausted_notified_at=datetime.now(timezone.utc),
    )
    refreshed = await _ensure_period(quota)
    assert refreshed.used_credits == 0
    assert refreshed.period_start == date.today().replace(day=1)
    assert refreshed.exhausted_notified_period_start is None
    assert refreshed.exhausted_notified_at is None


@pytest.mark.asyncio
async def test_quota_filter_notifies_only_once_per_period(db_init: object) -> None:
    chat = await _create_chat(-1001004, "Exhausted")
    period_start = date.today().replace(day=1)
    period_end = date.today().replace(day=28) + timedelta(days=4)
    period_end = period_end - timedelta(days=period_end.day)
    quota = AIQuotaModel(chat=chat, period_start=period_start, monthly_credits=1, used_credits=1)

    message = AsyncMock()
    message.reply = AsyncMock()
    quota_filter = AIQuotaFilter(AI_FEATURE_TRANSLATE)

    with (
        pytest.MonkeyPatch.context() as monkeypatch,
        pytest.raises(SkipHandler),
    ):
        monkeypatch.setattr(AIQuotaModel, "save", AsyncMock(return_value=quota))
        monkeypatch.setattr(
            "sophie_bot.modules.ai.filters.quota.check_quota",
            AsyncMock(return_value=SimpleNamespace(allowed=False, remaining=0, exhausted=True)),
        )
        monkeypatch.setattr(
            "sophie_bot.modules.ai.filters.quota.get_quota_info",
            AsyncMock(return_value=SimpleNamespace(period_start=period_start, period_end=period_end, total_credits=1)),
        )
        monkeypatch.setattr(
            "sophie_bot.modules.ai.filters.quota.AIQuotaModel.get_for_chat", AsyncMock(return_value=quota)
        )
        await quota_filter(message, chat)

    with (
        pytest.MonkeyPatch.context() as monkeypatch,
        pytest.raises(SkipHandler),
    ):
        monkeypatch.setattr(AIQuotaModel, "save", AsyncMock(return_value=quota))
        monkeypatch.setattr(
            "sophie_bot.modules.ai.filters.quota.check_quota",
            AsyncMock(return_value=SimpleNamespace(allowed=False, remaining=0, exhausted=True)),
        )
        monkeypatch.setattr(
            "sophie_bot.modules.ai.filters.quota.get_quota_info",
            AsyncMock(return_value=SimpleNamespace(period_start=period_start, period_end=period_end, total_credits=1)),
        )
        monkeypatch.setattr(
            "sophie_bot.modules.ai.filters.quota.AIQuotaModel.get_for_chat", AsyncMock(return_value=quota)
        )
        await quota_filter(message, chat)

    assert message.reply.await_count == 1
    assert quota.exhausted_notified_period_start == period_start
    assert quota.exhausted_notified_at is not None


@pytest.mark.asyncio
async def test_charge_ai_usage_records_requests_and_credits(db_init: object) -> None:
    chat = await _create_chat(-1001005, "Tracking")
    usage = SimpleNamespace(total_tokens=200, request_tokens=100, response_tokens=100)

    await charge_ai_usage(
        chat.iid, AI_FEATURE_CHATBOT, SimpleNamespace(model_name="mistralai/mistral-small-2603"), usage
    )

    all_usages = await AIUsageModel.find_all().to_list()
    assert all_usages
    ai_usage = all_usages[0]

    month_key = date.today().strftime("%Y-%m")
    assert ai_usage.daily_requests[date.today()] == 1
    assert ai_usage.monthly_requests_by_feature[month_key][AI_FEATURE_CHATBOT] == 1
    assert ai_usage.monthly_credits_by_feature[month_key][AI_FEATURE_CHATBOT] == 2
