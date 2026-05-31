from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatMemberStatus
from aiogram.types import Chat, MessageReactionUpdated, ReactionTypeEmoji, User

from sophie_bot.modules.locks.middlewares import reaction_enforcer
from sophie_bot.modules.locks.middlewares.reaction_enforcer import ReactionLocksEnforcerMiddleware
from sophie_bot.modules.locks.utils.lock_types import LockType


def _reaction_event(**kwargs: Any) -> MessageReactionUpdated:
    return MessageReactionUpdated(
        chat=Chat(id=-1001234567890, type="supergroup"),
        message_id=12,
        date=datetime.now(),
        old_reaction=[],
        new_reaction=[ReactionTypeEmoji(emoji="\N{THUMBS UP SIGN}")],
        user=User(id=42, is_bot=False, first_name="User"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_reaction_lock_removes_outsider_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    bot.get_chat_member.return_value = SimpleNamespace(status=ChatMemberStatus.LEFT)
    chat = SimpleNamespace(iid="chat-db-id")
    handler = AsyncMock()

    monkeypatch.setattr(reaction_enforcer, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(reaction_enforcer.ChatModel, "get_by_tid", AsyncMock(return_value=chat))
    monkeypatch.setattr(reaction_enforcer, "get_cached_locks", AsyncMock(return_value={LockType.OUTSIDE_REACTION}))

    with pytest.raises(SkipHandler):
        await ReactionLocksEnforcerMiddleware()(handler, _reaction_event(), {"bot": bot})

    bot.get_chat_member.assert_awaited_once_with(chat_id=-1001234567890, user_id=42)
    bot.set_message_reaction.assert_awaited_once_with(chat_id=-1001234567890, message_id=12, reaction=[])
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaction_lock_allows_chat_members(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    bot.get_chat_member.return_value = SimpleNamespace(status=ChatMemberStatus.MEMBER)
    chat = SimpleNamespace(iid="chat-db-id")
    handler = AsyncMock(return_value="handled")

    monkeypatch.setattr(reaction_enforcer, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(reaction_enforcer.ChatModel, "get_by_tid", AsyncMock(return_value=chat))
    monkeypatch.setattr(reaction_enforcer, "get_cached_locks", AsyncMock(return_value={LockType.OUTSIDE_REACTION}))

    result = await ReactionLocksEnforcerMiddleware()(handler, _reaction_event(), {"bot": bot})

    assert result == "handled"
    bot.set_message_reaction.assert_not_awaited()
    handler.assert_awaited_once()
