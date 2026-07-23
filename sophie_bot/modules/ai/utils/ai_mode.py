from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.ai.utils.cache_messages import reset_messages


@dataclass(frozen=True, slots=True)
class ModeCapabilities:
    """Everything the AI is allowed to do in a chat, derived from its mode.

    Resolved on every read rather than stored, so changing this table changes behaviour for all
    chats at once. The agent may never write or delete notes in any mode.
    """

    chatbot_for_users: bool
    trigger_on_reply: bool
    proactive_replies: bool
    notes_read: bool
    memory: bool
    moderator: bool
    message_cache: bool

    @property
    def ai_enabled(self) -> bool:
        return self is not _DISABLED


_DISABLED = ModeCapabilities(
    chatbot_for_users=False,
    trigger_on_reply=False,
    proactive_replies=False,
    notes_read=False,
    memory=False,
    moderator=False,
    message_cache=False,
)

_CAPABILITIES: Mapping[AIMode, ModeCapabilities] = {
    AIMode.disabled: _DISABLED,
    AIMode.entertainment: ModeCapabilities(
        chatbot_for_users=True,
        trigger_on_reply=True,
        proactive_replies=True,
        notes_read=True,
        memory=True,
        moderator=False,
        message_cache=True,
    ),
    # Moderation is the privacy mode: no message history is retained at all.
    AIMode.moderation: ModeCapabilities(
        chatbot_for_users=False,
        trigger_on_reply=False,
        proactive_replies=False,
        notes_read=False,
        memory=False,
        moderator=True,
        message_cache=False,
    ),
    AIMode.support: ModeCapabilities(
        chatbot_for_users=True,
        trigger_on_reply=True,
        proactive_replies=False,
        notes_read=True,
        memory=False,
        moderator=True,
        message_cache=True,
    ),
}


def get_capabilities(mode: AIMode) -> ModeCapabilities:
    return _CAPABILITIES[mode]


async def get_chat_mode(chat_iid: PydanticObjectId, default: AIMode = AIMode.support) -> AIMode:
    """The chat's mode, or ``default`` when it never picked one.

    The default differs by caller on purpose: a group that never picked a mode has AI off, while
    code that resolves a model has already established the AI is allowed to run, so it wants a
    usable tier rather than ``disabled``.
    """
    return await AIModeModel.get_mode(chat_iid) or default


async def resolve_chat_mode(chat: ChatModel) -> AIMode:
    """The mode governing what the AI may do in a chat. Private chats are always on the support tier."""
    if chat.type == ChatType.private:
        return AIMode.support
    return await get_chat_mode(chat.iid, AIMode.disabled)


async def resolve_chat_capabilities(chat: ChatModel) -> ModeCapabilities:
    return get_capabilities(await resolve_chat_mode(chat))


async def set_chat_mode(chat: ChatModel, mode: AIMode) -> None:
    await AIModeModel.set_mode(chat, mode)

    # Entering a mode that keeps no history must not leave the previous mode's messages behind.
    if not get_capabilities(mode).message_cache:
        await reset_messages(chat.tid)
