from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field

from sophie_bot.db.models._link_type import Link
from .chat import ChatModel
from .filters import FilterActionType


class WarnSettingsModel(Document):
    chat: Link[ChatModel]
    max_warns: int = Field(default=3, ge=2, le=10000)
    actions: list[FilterActionType] = []

    class Settings:
        name = "warn_settings"

    @staticmethod
    async def get_or_create(chat_iid: PydanticObjectId) -> WarnSettingsModel:
        # Cast to check if it helps, otherwise might need ignore
        if settings := await WarnSettingsModel.find_one(WarnSettingsModel.chat.id == chat_iid):
            return settings
        return WarnSettingsModel(chat=chat_iid)


class WarnModel(Document):
    chat: Link[ChatModel]
    user: Link[ChatModel]
    admin: Link[ChatModel]
    reason: Optional[str] = None
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "warns"

    @staticmethod
    async def get_user_warns(chat_iid: PydanticObjectId, user_iid: PydanticObjectId) -> list[WarnModel]:
        return (
            await WarnModel.find(WarnModel.chat.id == chat_iid, WarnModel.user.id == user_iid)
            .sort(WarnModel.date)
            .fetch_all_links()
            .to_list()
        )

    @staticmethod
    async def count_user_warns(chat_iid: PydanticObjectId, user_iid: PydanticObjectId) -> int:
        return await WarnModel.find(WarnModel.chat.id == chat_iid, WarnModel.user.id == user_iid).count()
