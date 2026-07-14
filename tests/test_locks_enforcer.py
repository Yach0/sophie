from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import Message

from sophie_bot.modules.locks.middlewares import enforcer
from sophie_bot.modules.locks.middlewares.enforcer import LocksEnforcerMiddleware
from sophie_bot.modules.locks.utils.lock_types import LockType

CHAT_ID = -1001234567890
USER_ID = 42


def _album_message(message_id: int) -> MagicMock:
    message = MagicMock(spec=Message)
    message.message_id = message_id
    message.chat = MagicMock()
    message.chat.id = CHAT_ID
    message.chat.type = ChatType.SUPERGROUP
    message.from_user = MagicMock()
    message.from_user.id = USER_ID
    message.delete = AsyncMock()
    return message


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enforcer, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(enforcer, "is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(enforcer, "get_cached_locks", AsyncMock(return_value={LockType.VIDEO}))


@pytest.mark.asyncio
async def test_locked_later_album_item_deletes_whole_album(monkeypatch: pytest.MonkeyPatch) -> None:
    """A locked item that is not the album representative still deletes the whole album."""
    _patch_common(monkeypatch)

    # Only the second album item matches a lock; the representative (first) does not.
    async def fake_check_locks(candidate: MagicMock, _locked_types: set[str]) -> str | None:
        return "video" if candidate.message_id == 2 else None

    monkeypatch.setattr(enforcer, "check_locks", fake_check_locks)

    album = [_album_message(1), _album_message(2), _album_message(3)]
    handler = AsyncMock()
    data = {"chat_db": SimpleNamespace(iid="chat-db-id"), "album": album}

    with pytest.raises(SkipHandler):
        await LocksEnforcerMiddleware()(handler, album[0], data)

    for message in album:
        message.delete.assert_awaited_once()
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_album_without_locked_items_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no album item is locked, the album is handled normally and nothing is deleted."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(enforcer, "check_locks", AsyncMock(return_value=None))

    album = [_album_message(1), _album_message(2)]
    handler = AsyncMock(return_value="handled")
    data = {"chat_db": SimpleNamespace(iid="chat-db-id"), "album": album}

    result = await LocksEnforcerMiddleware()(handler, album[0], data)

    assert result == "handled"
    for message in album:
        message.delete.assert_not_awaited()
    handler.assert_awaited_once()
