from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from beanie import Document, PydanticObjectId
from pydantic import Field, field_validator

from sophie_bot.constants import WARN_MAX_ACTIONS
from sophie_bot.db.models._link_type import Link

from .chat import ChatModel
from .filters import FilterActionType


class WarnSettingsModel(Document):
    chat: Link[ChatModel]
    max_warns: int = Field(default=3, ge=2, le=10000)
    on_each_warn_actions: list[FilterActionType] = Field(default_factory=list)
    on_max_warn_actions: list[FilterActionType] = Field(default_factory=list)

    class Settings:
        name = "warn_settings"

    @staticmethod
    async def _find_by_chat_iid(chat_iid: PydanticObjectId) -> WarnSettingsModel | None:
        by_link_id = await WarnSettingsModel.find_one(WarnSettingsModel.chat.id == chat_iid)
        if by_link_id:
            return by_link_id

        return await WarnSettingsModel.find_one(WarnSettingsModel.chat == chat_iid)

    @staticmethod
    async def get_or_create(chat_iid: PydanticObjectId) -> WarnSettingsModel:
        if settings := await WarnSettingsModel._find_by_chat_iid(chat_iid):
            return settings

        chat = await ChatModel.get_by_iid(chat_iid)
        if chat is None:
            raise ValueError(f"Chat with internal ID {chat_iid!s} not found")

        settings = WarnSettingsModel(chat=chat)
        await settings.save()
        return settings

    @staticmethod
    async def get_by_chat_iid(chat_iid: PydanticObjectId) -> WarnSettingsModel:
        return await WarnSettingsModel.get_or_create(chat_iid)

    @staticmethod
    def _upsert_action(
        actions: list[FilterActionType], action_name: str, action_data: dict | None = None
    ) -> list[FilterActionType]:
        action = FilterActionType(name=action_name, data=action_data or {})
        new_actions = list(actions)

        for index, existing in enumerate(new_actions):
            if existing.name == action_name:
                new_actions[index] = action
                return new_actions

        if len(new_actions) >= WARN_MAX_ACTIONS:
            new_actions.pop(0)

        new_actions.append(action)
        return new_actions

    @staticmethod
    async def add_on_each_warn_action(
        chat_iid: PydanticObjectId, action_name: str, action_data: dict | None = None
    ) -> WarnSettingsModel:
        model = await WarnSettingsModel.get_or_create(chat_iid)
        model.on_each_warn_actions = WarnSettingsModel._upsert_action(
            model.on_each_warn_actions, action_name, action_data
        )
        await model.save()
        return model

    @staticmethod
    async def remove_on_each_warn_action(chat_iid: PydanticObjectId, action_name: str) -> WarnSettingsModel:
        model = await WarnSettingsModel.get_or_create(chat_iid)
        model.on_each_warn_actions = [action for action in model.on_each_warn_actions if action.name != action_name]
        await model.save()
        return model

    @staticmethod
    async def add_on_max_warn_action(
        chat_iid: PydanticObjectId, action_name: str, action_data: dict | None = None
    ) -> WarnSettingsModel:
        model = await WarnSettingsModel.get_or_create(chat_iid)
        model.on_max_warn_actions = WarnSettingsModel._upsert_action(
            model.on_max_warn_actions, action_name, action_data
        )
        await model.save()
        return model

    @staticmethod
    async def remove_on_max_warn_action(chat_iid: PydanticObjectId, action_name: str) -> WarnSettingsModel:
        model = await WarnSettingsModel.get_or_create(chat_iid)
        model.on_max_warn_actions = [action for action in model.on_max_warn_actions if action.name != action_name]
        await model.save()
        return model


class WarnModel(Document):
    chat: Link[ChatModel]
    user: Link[ChatModel]
    admin: Link[ChatModel] | None = None
    reason: str | None = None
    date: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("admin", mode="before")
    @classmethod
    def _coerce_legacy_admin(cls, value: Any) -> Any:
        if value is None or (isinstance(value, dict) and not value):
            return None
        return value

    class Settings:
        name = "warns"

    @staticmethod
    async def get_user_warns(chat_iid: PydanticObjectId, user_iid: PydanticObjectId) -> list[WarnModel]:
        return (
            await WarnModel.find(WarnModel.chat.id == chat_iid, WarnModel.user.id == user_iid, fetch_links=True)
            .sort(WarnModel.date)
            .to_list()
        )

    @staticmethod
    async def count_user_warns(chat_iid: PydanticObjectId, user_iid: PydanticObjectId) -> int:
        return await WarnModel.find(WarnModel.chat.id == chat_iid, WarnModel.user.id == user_iid).count()
