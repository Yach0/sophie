from __future__ import annotations

from typing import ClassVar

from beanie import Document, PydanticObjectId
from pydantic import ConfigDict, Field, field_validator

from ._link_type import Link
from .chat import ChatModel
from .filters import FilterActionType


class AntifloodModel(Document):
    chat: Link[ChatModel]
    enabled: bool | None = True
    message_count: int = Field(default=5, ge=1, le=100, alias="count")
    actions: list[FilterActionType] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)

    class Settings:
        name = "antiflood"
        bson_encoders: ClassVar = {}

    @field_validator("message_count", mode="before")
    @classmethod
    def handle_legacy_count(cls, value: object) -> int:
        if isinstance(value, int):
            return value
        return 5

    @staticmethod
    async def get_by_chat_iid(chat_iid: PydanticObjectId) -> AntifloodModel:
        """Get or create antiflood settings for a chat using database internal ID."""
        existing = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)
        if existing:
            return existing

        return AntifloodModel(chat=chat_iid)

    @staticmethod
    async def set_antiflood_count(chat_iid: PydanticObjectId, message_count: int) -> AntifloodModel:
        """Set the message count threshold for antiflood using internal DB ID (chat_iid)."""
        model = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid) or AntifloodModel(chat=chat_iid)

        model.message_count = message_count
        await model.save()
        return model

    @staticmethod
    async def add_antiflood_action(
        chat_iid: PydanticObjectId, action_name: str, action_data: dict | None = None
    ) -> AntifloodModel:
        """Add an action for antiflood violations using internal DB ID (chat_iid)."""
        action = FilterActionType(name=action_name, data=action_data or {})

        model = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)
        if model is None:
            chat = await ChatModel.get_by_iid(chat_iid)
            if chat is None:
                raise ValueError(f"Chat with internal ID {chat_iid!s} not found")
            model = AntifloodModel(chat=chat)

        actions = list(model.actions or [])
        actions.append(action)
        model.actions = actions

        await model.save()
        return model

    @staticmethod
    async def remove_antiflood_action(chat_iid: PydanticObjectId, action_name: str) -> AntifloodModel:
        """Remove an action for antiflood violations using internal DB ID (chat_iid)."""
        model = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)
        if model is None:
            chat = await ChatModel.get_by_iid(chat_iid)
            if chat is None:
                raise ValueError(f"Chat with internal ID {chat_iid!s} not found")
            model = AntifloodModel(chat=chat)
            # No actions to remove on a fresh model
            await model.save()
            return model

        model.actions = [action for action in model.actions if action.name != action_name]
        await model.save()
        return model
