from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.dispatcher.event.handler import CallableObject
from aiogram.types import Chat, Message

from sophie_bot.db.models.chat import ChatType
from sophie_bot.filters import feature_flag
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.middlewares.connections import ChatConnection

PRIVATE_CHAT_ID = 42
GROUP_CHAT_ID = -1001234567890


def _pm_message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=PRIVATE_CHAT_ID, type="private"),
        text="/lock url",
    )


def _connection(chat_tid: int, is_connected: bool) -> ChatConnection:
    db_model = MagicMock()
    db_model.tid = chat_tid
    return ChatConnection(
        type=ChatType.supergroup,
        is_connected=is_connected,
        tid=chat_tid,
        title="Test group",
        db_model=db_model,
    )


@pytest.mark.asyncio
async def test_connected_chat_is_used_when_dispatched_through_aiogram(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PM user connected to a group must have the flag evaluated against the group.

    Dispatched via CallableObject so aiogram's own kwarg filtering decides what reaches the filter.
    """
    is_enabled_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(feature_flag, "is_enabled", is_enabled_mock)

    callable_object = CallableObject(callback=FeatureFlagFilter("locks"))
    result = await callable_object.call(
        _pm_message(),
        connection=_connection(GROUP_CHAT_ID, is_connected=True),
        bot=MagicMock(),
        event_chat=MagicMock(),
    )

    assert result is True
    is_enabled_mock.assert_awaited_once_with("locks", chat_tid=GROUP_CHAT_ID)


@pytest.mark.asyncio
async def test_unconnected_pm_uses_the_pm_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    is_enabled_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(feature_flag, "is_enabled", is_enabled_mock)

    callable_object = CallableObject(callback=FeatureFlagFilter("locks"))
    result = await callable_object.call(
        _pm_message(),
        connection=_connection(PRIVATE_CHAT_ID, is_connected=False),
        bot=MagicMock(),
    )

    assert result is True
    is_enabled_mock.assert_awaited_once_with("locks", chat_tid=PRIVATE_CHAT_ID)


@pytest.mark.asyncio
async def test_falls_back_to_event_chat_without_a_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    is_enabled_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(feature_flag, "is_enabled", is_enabled_mock)

    result = await FeatureFlagFilter("locks", enabled=False)(_pm_message())

    assert result is True
    is_enabled_mock.assert_awaited_once_with("locks", chat_tid=PRIVATE_CHAT_ID)
