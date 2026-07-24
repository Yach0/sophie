from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime

from beanie import PydanticObjectId

from sophie_bot.constants import AI_CREDITS_PER_TOKEN
from sophie_bot.db.models import AIQuotaModel, AIUsageModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode
from sophie_bot.modules.ai.utils.ai_model_pricing import estimate_model_credit_cost
from sophie_bot.utils.ai_features import AIFeature
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.logger import log


@dataclass(frozen=True)
class QuotaInfo:
    total_credits: int
    used_credits: int
    remaining_credits: int
    period_start: date
    period_end: date


@dataclass(frozen=True)
class QuotaCheckResult:
    allowed: bool
    remaining: int
    exhausted: bool


@dataclass(frozen=True)
class AIQuotaState:
    quota: AIQuotaModel
    usage: AIUsageModel | None
    month_key: str
    boost_credits: int = 0

    @property
    def total_credits(self) -> int:
        return self.quota.total_credits + self.boost_credits

    @property
    def used_credits(self) -> int:
        return self.quota.used_credits_amount

    @property
    def remaining_credits(self) -> int:
        return max(self.total_credits - self.used_credits, 0)


def _current_period_start() -> date:
    return datetime.now(UTC).date().replace(day=1)


def _get_period_end(period_start: date) -> date:
    last_day = calendar.monthrange(period_start.year, period_start.month)[1]
    return date(period_start.year, period_start.month, last_day)


def get_period_end(period_start: date) -> date:
    return _get_period_end(period_start)


async def _ensure_period(quota: AIQuotaModel) -> AIQuotaModel:
    current = _current_period_start()
    if quota.period_start < current:
        quota.used_credits = 0
        quota.period_start = current
        quota.exhausted_notified_period_start = None
        quota.exhausted_notified_at = None
        await quota.save()
    return quota


async def get_or_create_quota_model(chat_iid: PydanticObjectId) -> AIQuotaModel | None:
    quota = await AIQuotaModel.get_for_chat(chat_iid)
    if quota:
        return await _ensure_period(quota)

    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        return None

    quota = await AIQuotaModel.get_or_create(chat)
    return await _ensure_period(quota)


async def get_entertainment_boost_credits(chat_iid: PydanticObjectId) -> int:
    """Extra monthly credits granted while a chat is in entertainment mode.

    Derived on every read instead of persisted, so turning ``ai_entertainment_boost`` off or
    lowering ``ai_entertainment_monthly_credits`` takes effect immediately for chats already in it.
    """
    if not await is_enabled("ai_entertainment_boost"):
        return 0
    if await get_chat_mode(chat_iid, AIMode.disabled) != AIMode.entertainment:
        return 0
    return max(int(await get_value("ai_entertainment_monthly_credits")), 0)


async def get_quota_state(chat_iid: PydanticObjectId) -> AIQuotaState | None:
    quota = await get_or_create_quota_model(chat_iid)
    if not quota:
        return None

    usage = await AIUsageModel.find_one(AIUsageModel.chat.id == chat_iid)
    return AIQuotaState(
        quota=quota,
        usage=usage,
        month_key=quota.period_start.strftime("%Y-%m"),
        boost_credits=await get_entertainment_boost_credits(chat_iid),
    )


async def get_quota_info(chat_iid: PydanticObjectId) -> QuotaInfo | None:
    state = await get_quota_state(chat_iid)
    if not state:
        return None

    return QuotaInfo(
        total_credits=state.total_credits,
        used_credits=state.used_credits,
        remaining_credits=state.remaining_credits,
        period_start=state.quota.period_start,
        period_end=_get_period_end(state.quota.period_start),
    )


async def check_quota(chat_iid: PydanticObjectId) -> QuotaCheckResult:
    state = await get_quota_state(chat_iid)
    if not state:
        return QuotaCheckResult(allowed=False, remaining=0, exhausted=False)

    if state.remaining_credits > 0:
        return QuotaCheckResult(allowed=True, remaining=state.remaining_credits, exhausted=False)

    return QuotaCheckResult(allowed=False, remaining=0, exhausted=True)


async def consume_quota(
    chat_iid: PydanticObjectId,
    feature: AIFeature,
    tokens: int,
    model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    if tokens <= 0:
        return

    quota = await get_or_create_quota_model(chat_iid)
    if not quota:
        return

    credits_used = (
        await estimate_model_credit_cost(model_name, tokens, input_tokens, output_tokens)
        if model_name
        else tokens_to_credits(tokens)
    )

    quota.used_credits += credits_used
    await quota.save()

    await AIUsageModel.record_feature_consumption(chat_iid, feature, credits_used)

    log.debug(
        "AI quota consumed",
        chat_iid=str(chat_iid),
        feature=feature,
        tokens=tokens,
        credits=credits_used,
        model_name=model_name,
    )


async def set_monthly_quota(chat: ChatModel, credits: int) -> AIQuotaModel:
    quota = await get_or_create_quota_model(chat.iid)
    if not quota:
        quota = AIQuotaModel(chat=chat, monthly_credits=credits, period_start=_current_period_start())
    else:
        quota.monthly_credits = credits

    return await quota.save()


async def reset_period_usage(chat_iid: PydanticObjectId) -> None:
    quota = await get_or_create_quota_model(chat_iid)
    if not quota:
        return

    quota.used_credits = 0
    quota.period_start = _current_period_start()
    quota.exhausted_notified_period_start = None
    quota.exhausted_notified_at = None
    await quota.save()


def tokens_to_credits(tokens: int) -> int:
    return (tokens + AI_CREDITS_PER_TOKEN - 1) // AI_CREDITS_PER_TOKEN
