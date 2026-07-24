from __future__ import annotations

from enum import Enum

from beanie import Document, PydanticObjectId

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class AIMode(str, Enum):
    """What Sophie's AI is for in a chat. Every AI behaviour is derived from this one choice.

    The last two are private-chat only and never stored: they are resolved per message, so they
    cannot be picked with /aimode and never appear in its keyboard.
    """

    disabled = "disabled"
    entertainment = "entertainment"
    moderation = "moderation"
    support = "support"
    sophie_pm = "sophie_pm"
    sophie_help = "sophie_help"


SELECTABLE_MODES: tuple[AIMode, ...] = (
    AIMode.disabled,
    AIMode.entertainment,
    AIMode.moderation,
    AIMode.support,
)


class AIModeModel(Document):
    chat: Link[ChatModel]
    mode: AIMode = AIMode.disabled

    class Settings:
        name = "ai_mode"

    @staticmethod
    async def get_mode(chat_iid: PydanticObjectId) -> AIMode | None:
        """The configured mode, or None when the chat never picked one."""
        model = await AIModeModel.find_one(AIModeModel.chat.id == chat_iid)
        return model.mode if model else None

    @staticmethod
    async def set_mode(chat: ChatModel, mode: AIMode) -> AIModeModel:
        model = await AIModeModel.find_one(AIModeModel.chat.id == chat.iid)
        if model:
            model.mode = mode
            return await model.save()

        return await AIModeModel(chat=chat, mode=mode).save()
