from __future__ import annotations

from datetime import UTC, date, datetime

from beanie import Document, PydanticObjectId
from pydantic import Field

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.utils.ai_features import (
    AIFeature,
)


class AIUsageModel(Document):
    chat: Link[ChatModel]
    daily_requests: dict[date, int] = Field(default_factory=dict)
    monthly_requests_by_feature: dict[str, dict[str, int]] = Field(default_factory=dict)
    monthly_credits_by_feature: dict[str, dict[str, int]] = Field(default_factory=dict)

    class Settings:
        name = "ai_usage"

    @staticmethod
    async def get_or_create_usage(chat_iid: PydanticObjectId) -> AIUsageModel | None:
        usage = await AIUsageModel.find_one(AIUsageModel.chat.id == chat_iid)
        if usage:
            return usage

        chat = await ChatModel.get_by_iid(chat_iid)
        if not chat:
            return None

        return AIUsageModel(chat=chat)

    @staticmethod
    async def get_today(chat_iid: PydanticObjectId) -> int:
        usage = await AIUsageModel.get_or_create_usage(chat_iid)

        if not usage:
            return 0

        return usage.daily_requests.get(datetime.now(UTC).date(), 0)

    @staticmethod
    async def get_monthly_feature_requests(
        chat_iid: PydanticObjectId, month_key: str | None = None
    ) -> dict[AIFeature, int]:
        if month_key is None:
            month_key = datetime.now(UTC).date().strftime("%Y-%m")

        usage = await AIUsageModel.find_one(AIUsageModel.chat.id == chat_iid)
        if not usage:
            return {}

        return usage.monthly_requests_by_feature.get(month_key, {})

    @staticmethod
    async def record_feature_consumption(chat_iid: PydanticObjectId, feature: AIFeature, credits: int) -> None:
        month_key = datetime.now(UTC).date().strftime("%Y-%m")
        date_today = datetime.now(UTC).date()

        usage = await AIUsageModel.get_or_create_usage(chat_iid)
        if not usage:
            return

        if month_key not in usage.monthly_requests_by_feature:
            usage.monthly_requests_by_feature[month_key] = {}
        if month_key not in usage.monthly_credits_by_feature:
            usage.monthly_credits_by_feature[month_key] = {}

        usage.daily_requests[date_today] = usage.daily_requests.get(date_today, 0) + 1
        usage.monthly_requests_by_feature[month_key][feature] = (
            usage.monthly_requests_by_feature[month_key].get(feature, 0) + 1
        )
        usage.monthly_credits_by_feature[month_key][feature] = (
            usage.monthly_credits_by_feature[month_key].get(feature, 0) + credits
        )
        await usage.save()

    @staticmethod
    async def get_monthly_feature_credits(
        chat_iid: PydanticObjectId, month_key: str | None = None
    ) -> dict[AIFeature, int]:
        if month_key is None:
            month_key = datetime.now(UTC).date().strftime("%Y-%m")

        usage = await AIUsageModel.find_one(AIUsageModel.chat.id == chat_iid)
        if not usage:
            return {}

        return usage.monthly_credits_by_feature.get(month_key, {})
