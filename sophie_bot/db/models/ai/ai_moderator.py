from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import ClassVar

from beanie import Document, PydanticObjectId
from beanie.odm.queries.update import UpdateResponse
from beanie.operators import Set
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class DetectionLevel(str, Enum):
    OFF = "off"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class AIModeratorModel(Document):
    chat: Link[ChatModel]
    # None follows the AI mode default; stored booleans are explicit chat overrides.
    enabled: bool | None = None

    sexual: DetectionLevel = DetectionLevel.NORMAL
    hate_and_discrimination: DetectionLevel = DetectionLevel.NORMAL
    violence_and_threats: DetectionLevel = DetectionLevel.NORMAL
    dangerous_and_criminal_content: DetectionLevel = DetectionLevel.NORMAL
    selfharm: DetectionLevel = DetectionLevel.NORMAL
    health: DetectionLevel = DetectionLevel.NORMAL
    financial: DetectionLevel = DetectionLevel.NORMAL
    law: DetectionLevel = DetectionLevel.NORMAL
    pii: DetectionLevel = DetectionLevel.NORMAL

    class Settings:
        name = "ai_moderator"
        indexes: ClassVar = [IndexModel("chat", unique=True, name="moderator_chat_unique")]

    @staticmethod
    async def get_state(chat_iid: PydanticObjectId) -> bool | None:
        usage = await AIModeratorModel.find_one(AIModeratorModel.chat.id == chat_iid)
        return usage.enabled if usage else None

    @staticmethod
    async def set_state(chat: ChatModel, new_state: bool) -> AIModeratorModel:
        return await AIModeratorModel.update_fields(chat, {"enabled": new_state})

    @staticmethod
    async def update_fields(chat: ChatModel, values: Mapping[str, bool | DetectionLevel | None]) -> AIModeratorModel:
        """Write only the requested fields; insertion defaults never replace existing settings."""
        on_insert = AIModeratorModel(chat=chat)
        for field, value in values.items():
            setattr(on_insert, field, value)
        query = AIModeratorModel.find_one(AIModeratorModel.chat.id == chat.iid)
        try:
            return await query.upsert(Set(values), on_insert=on_insert, response_type=UpdateResponse.NEW_DOCUMENT)
        except DuplicateKeyError:
            # Another worker inserted this chat after the upsert's update found no record.
            return await query.update(Set(values), response_type=UpdateResponse.NEW_DOCUMENT)
