from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.greetings import WELCOMESECURITY_EXPIRE_DEFAULT_TIME
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers

_MODULE = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"


def _make_ws_user(*, is_join_request: bool, age_hours: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        passed=False,
        is_join_request=is_join_request,
        added_at=datetime.now(UTC) - timedelta(hours=age_hours),
        user=SimpleNamespace(ref=SimpleNamespace(id=PydanticObjectId())),
        group=SimpleNamespace(ref=SimpleNamespace(id=PydanticObjectId())),
        delete=AsyncMock(),
    )


def _patch_module(
    monkeypatch: pytest.MonkeyPatch,
    kick_user: AsyncMock,
    bot: SimpleNamespace,
    *,
    expiry: timedelta | None = WELCOMESECURITY_EXPIRE_DEFAULT_TIME,
) -> None:
    monkeypatch.setattr(
        f"{_MODULE}.ChatModel.get_by_iid",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id=PydanticObjectId(), iid=PydanticObjectId(), tid=123),
                SimpleNamespace(id=PydanticObjectId(), iid=PydanticObjectId(), tid=-100123),
            ]
        ),
    )
    monkeypatch.setattr(f"{_MODULE}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        f"{_MODULE}.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=SimpleNamespace(welcome_security=SimpleNamespace(expire=expiry))),
    )
    monkeypatch.setattr(f"{_MODULE}.kick_user", kick_user)
    monkeypatch.setattr(f"{_MODULE}.bot", bot)


@pytest.mark.asyncio
async def test_process_user_kicks_timed_out_non_join_request_user(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_user = _make_ws_user(
        is_join_request=False,
        age_hours=WELCOMESECURITY_EXPIRE_DEFAULT_TIME.total_seconds() / 3600 + 1,
    )
    kick_user = AsyncMock()
    bot = SimpleNamespace(decline_chat_join_request=AsyncMock())
    _patch_module(monkeypatch, kick_user, bot)

    await KickUnpassedUsers.process_user(ws_user)

    kick_user.assert_awaited_once_with(chat_tid=-100123, user_tid=123)
    bot.decline_chat_join_request.assert_not_awaited()
    ws_user.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_user_declines_timed_out_join_request(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_user = _make_ws_user(
        is_join_request=True,
        age_hours=WELCOMESECURITY_EXPIRE_DEFAULT_TIME.total_seconds() / 3600 + 1,
    )
    kick_user = AsyncMock()
    bot = SimpleNamespace(decline_chat_join_request=AsyncMock())
    _patch_module(monkeypatch, kick_user, bot)

    await KickUnpassedUsers.process_user(ws_user)

    bot.decline_chat_join_request.assert_awaited_once_with(chat_id=-100123, user_id=123)
    kick_user.assert_not_awaited()
    ws_user.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_user_leaves_user_inside_timeout_window(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_user = _make_ws_user(is_join_request=False, age_hours=1)
    kick_user = AsyncMock()
    bot = SimpleNamespace(decline_chat_join_request=AsyncMock())
    _patch_module(monkeypatch, kick_user, bot)

    await KickUnpassedUsers.process_user(ws_user)

    kick_user.assert_not_awaited()
    ws_user.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_user_uses_group_specific_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_user = _make_ws_user(is_join_request=False, age_hours=3)
    kick_user = AsyncMock()
    bot = SimpleNamespace(decline_chat_join_request=AsyncMock())
    _patch_module(monkeypatch, kick_user, bot, expiry=timedelta(hours=2))

    await KickUnpassedUsers.process_user(ws_user)

    kick_user.assert_awaited_once_with(chat_tid=-100123, user_tid=123)
    ws_user.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_user_defaults_missing_expiry_to_48_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    ws_user = _make_ws_user(is_join_request=False, age_hours=3)
    kick_user = AsyncMock()
    bot = SimpleNamespace(decline_chat_join_request=AsyncMock())
    _patch_module(monkeypatch, kick_user, bot, expiry=None)

    await KickUnpassedUsers.process_user(ws_user)

    kick_user.assert_not_awaited()
    ws_user.delete.assert_not_awaited()
