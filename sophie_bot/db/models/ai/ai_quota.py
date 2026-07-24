from __future__ import annotations

from datetime import UTC, date, datetime

from beanie import Document, PydanticObjectId

from sophie_bot.constants import AI_DEFAULT_MONTHLY_CREDITS
from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class AIQuotaModel(Document):
    chat: Link[ChatModel]
    monthly_credits: int | None = AI_DEFAULT_MONTHLY_CREDITS
    bonus_credits: int = 0
    period_start: date
    used_credits: int = 0
    exhausted_notified_period_start: date | None = None
    exhausted_notified_at: datetime | None = None

    class Settings:
        name = "ai_quota"

    @property
    def total_credits(self) -> int:
        base_credits = self.monthly_credits or AI_DEFAULT_MONTHLY_CREDITS
        return base_credits + self.bonus_credits

    @property
    def used_credits_amount(self) -> int:
        return self.used_credits

    @property
    def remaining_credits(self) -> int:
        return self.total_credits - self.used_credits_amount

    @staticmethod
    async def get_or_create(chat: ChatModel) -> AIQuotaModel:
        quota = await AIQuotaModel.find_one(AIQuotaModel.chat.id == chat.iid)
        if quota:
            return quota

        quota = AIQuotaModel(chat=chat, monthly_credits=None, period_start=datetime.now(UTC).date().replace(day=1))
        return await quota.save()

    @staticmethod
    async def get_for_chat(chat_iid: PydanticObjectId) -> AIQuotaModel | None:
        return await AIQuotaModel.find_one(AIQuotaModel.chat.id == chat_iid)
