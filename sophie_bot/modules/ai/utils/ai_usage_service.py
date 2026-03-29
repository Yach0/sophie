from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Protocol, cast

from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import In

from sophie_bot.db.models import AIUsageModel, ChatModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.ai.utils.ai_quota import consume_quota, get_period_end, get_quota_state
from sophie_bot.utils.ai_features import AIFeature, AI_FEATURES_BY_KEY


class AIUsageLike(Protocol):
    total_tokens: int
    request_tokens: int
    response_tokens: int


@dataclass(frozen=True)
class ChatUsageBreakdownItem:
    feature: AIFeature
    title: str
    icon: str
    credits: int
    percentage: int


@dataclass(frozen=True)
class ChatUsageView:
    total_credits: int
    used_credits: int
    remaining_credits: int
    percentage_remaining: int
    period_end: date
    breakdown: tuple[ChatUsageBreakdownItem, ...]


@dataclass(frozen=True)
class OperatorFeatureStats:
    feature: AIFeature
    title: str
    icon: str
    requests: int
    credits: int


@dataclass(frozen=True)
class OperatorAIStats:
    total_requests_today: int
    total_requests_week: int
    total_requests_month: int
    total_credits_month: int
    top_chats_by_requests: tuple[tuple[ChatModel, int], ...]
    top_chats_by_credits: tuple[tuple[ChatModel, int], ...]
    top_users_by_requests: tuple[tuple[ChatModel, int], ...]
    top_users_by_credits: tuple[tuple[ChatModel, int], ...]
    top_features: tuple[OperatorFeatureStats, ...]


class AIModelLike(Protocol):
    model_name: str


async def charge_ai_usage(
    chat_iid: PydanticObjectId, feature: AIFeature, model: AIModelLike, usage: AIUsageLike
) -> None:
    total_tokens = usage.total_tokens if usage.total_tokens else 0
    if total_tokens <= 0:
        return

    input_tokens = usage.request_tokens if usage.request_tokens else None
    output_tokens = usage.response_tokens if usage.response_tokens else None

    await consume_quota(
        chat_iid,
        feature,
        total_tokens,
        model_name=model.model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def get_chat_usage_view(chat_iid: PydanticObjectId) -> ChatUsageView | None:
    quota_state = await get_quota_state(chat_iid)
    if not quota_state:
        return None

    feature_credits = (
        quota_state.usage.monthly_credits_by_feature.get(quota_state.month_key, {}) if quota_state.usage else {}
    )
    total_feature_credits = sum(feature_credits.values())

    breakdown_items: list[ChatUsageBreakdownItem] = []
    for feature, credits in sorted(feature_credits.items(), key=lambda item: item[1], reverse=True):
        feature_key = cast(AIFeature, feature)
        info = AI_FEATURES_BY_KEY[feature_key]
        percentage = int((credits / total_feature_credits) * 100) if total_feature_credits > 0 else 0
        breakdown_items.append(
            ChatUsageBreakdownItem(
                feature=feature_key,
                title=info.title,
                icon=info.icon,
                credits=credits,
                percentage=percentage,
            )
        )

    percentage_remaining = int(
        (quota_state.quota.remaining_credits / quota_state.quota.total_credits) * 100
        if quota_state.quota.total_credits > 0
        else 0
    )
    return ChatUsageView(
        total_credits=quota_state.quota.total_credits,
        used_credits=quota_state.quota.used_credits_amount,
        remaining_credits=quota_state.quota.remaining_credits,
        percentage_remaining=percentage_remaining,
        period_end=get_period_end(quota_state.quota.period_start),
        breakdown=tuple(breakdown_items),
    )


def _sum_in_range(days: dict[date, int], start: date, end: date) -> int:
    return sum(value for current_date, value in days.items() if start <= current_date <= end)


def _top_n(items: dict[str, int], count: int) -> list[tuple[str, int]]:
    return sorted(items.items(), key=lambda item: item[1], reverse=True)[:count]


async def get_operator_ai_stats() -> OperatorAIStats:
    today = date.today()
    start_week = today - timedelta(days=today.weekday())
    start_month = today.replace(day=1)

    usages = await AIUsageModel.find_all().to_list()

    total_requests_today = 0
    total_requests_week = 0
    total_requests_month = 0
    total_credits_month = 0
    chats_today_requests: dict[str, int] = {}
    chats_month_requests: dict[str, int] = {}
    chats_month_credits: dict[str, int] = {}
    iids_needed: set[str] = set()
    feature_requests: dict[AIFeature, int] = {}
    feature_credits: dict[AIFeature, int] = {}
    month_key = start_month.strftime("%Y-%m")

    for usage in usages:
        today_count = _sum_in_range(usage.daily_requests, today, today)
        week_count = _sum_in_range(usage.daily_requests, start_week, today)
        month_count = _sum_in_range(usage.daily_requests, start_month, today)
        monthly_credits = sum(usage.monthly_credits_by_feature.get(month_key, {}).values())
        iid_str = str(usage.chat.ref.id)

        if today_count > 0:
            total_requests_today += today_count
            chats_today_requests[iid_str] = chats_today_requests.get(iid_str, 0) + today_count
            iids_needed.add(iid_str)
        if week_count > 0:
            total_requests_week += week_count
            iids_needed.add(iid_str)
        if month_count > 0:
            total_requests_month += month_count
            chats_month_requests[iid_str] = chats_month_requests.get(iid_str, 0) + month_count
            chats_month_credits[iid_str] = chats_month_credits.get(iid_str, 0) + monthly_credits
            iids_needed.add(iid_str)

        total_credits_month += monthly_credits
        for feature, requests in usage.monthly_requests_by_feature.get(month_key, {}).items():
            feature_key = cast(AIFeature, feature)
            feature_requests[feature_key] = feature_requests.get(feature_key, 0) + requests
        for feature, credits in usage.monthly_credits_by_feature.get(month_key, {}).items():
            feature_key = cast(AIFeature, feature)
            feature_credits[feature_key] = feature_credits.get(feature_key, 0) + credits

    chat_by_id: dict[str, ChatModel] = {}
    if iids_needed:
        ids = [PydanticObjectId(value) for value in iids_needed]
        chat_models = await ChatModel.find(In(ChatModel.iid, ids)).to_list()
        chat_by_id = {str(chat.iid): chat for chat in chat_models}

    def _filter_by_types(data: dict[str, int], allowed_types: set[ChatType]) -> dict[str, int]:
        filtered: dict[str, int] = {}
        for iid, value in data.items():
            chat = chat_by_id.get(iid)
            if chat and chat.type in allowed_types:
                filtered[iid] = value
        return filtered

    def _materialize(entries: Iterable[tuple[str, int]]) -> tuple[tuple[ChatModel, int], ...]:
        materialized: list[tuple[ChatModel, int]] = []
        for iid, count in entries:
            chat = chat_by_id.get(iid)
            if chat:
                materialized.append((chat, count))
        return tuple(materialized)

    def _build_feature_stats() -> tuple[OperatorFeatureStats, ...]:
        items: list[OperatorFeatureStats] = []
        for feature, credits in sorted(feature_credits.items(), key=lambda item: item[1], reverse=True):
            info = AI_FEATURES_BY_KEY[feature]
            items.append(
                OperatorFeatureStats(
                    feature=feature,
                    title=info.title,
                    icon=info.icon,
                    requests=feature_requests.get(feature, 0),
                    credits=credits,
                )
            )
        return tuple(items)

    chat_types = {ChatType.group, ChatType.supergroup, ChatType.channel}
    return OperatorAIStats(
        total_requests_today=total_requests_today,
        total_requests_week=total_requests_week,
        total_requests_month=total_requests_month,
        total_credits_month=total_credits_month,
        top_chats_by_requests=_materialize(_top_n(_filter_by_types(chats_month_requests, chat_types), 5)),
        top_chats_by_credits=_materialize(_top_n(_filter_by_types(chats_month_credits, chat_types), 5)),
        top_users_by_requests=_materialize(_top_n(_filter_by_types(chats_month_requests, {ChatType.private}), 5)),
        top_users_by_credits=_materialize(_top_n(_filter_by_types(chats_month_credits, {ChatType.private}), 5)),
        top_features=_build_feature_stats(),
    )
