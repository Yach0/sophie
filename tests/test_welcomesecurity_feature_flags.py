from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from beanie import PydanticObjectId

from sophie_bot.modules.greetings.middlewares.new_user import NewUserMiddleware
from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers
from sophie_bot.modules.welcomesecurity.schedules.ban_unpassed_users import BanUnpassedUsers


@pytest.mark.asyncio
async def test_new_user_middleware_falls_back_to_regular_welcome_when_captcha_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = NewUserMiddleware()
    chat_iid = PydanticObjectId()
    user_iid = PydanticObjectId()
    event = SimpleNamespace(
        new_chat_members=[SimpleNamespace(id=123)],
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(id=-100123, join_by_request=False),
        date=None,
        message_id=77,
    )
    chat_db = SimpleNamespace(iid=chat_iid, tid=event.chat.id)
    new_users = [SimpleNamespace(iid=user_iid, tid=123, is_bot=False)]
    greetings = SimpleNamespace(
        welcome_disabled=False,
        welcome_security=SimpleNamespace(enabled=True),
        note=SimpleNamespace(text="Welcome"),
        clean_service=None,
        clean_welcome=None,
        welcome_mute=None,
    )
    send_welcome = AsyncMock(return_value=SimpleNamespace(message_id=501))
    captcha_handler = AsyncMock()
    handler = AsyncMock()

    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.new_user.NewUserMiddleware.is_join_request",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("sophie_bot.modules.greetings.middlewares.new_user.Message", SimpleNamespace)
    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.new_user.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=greetings),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.new_user.RulesModel.get_rules",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.new_user.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.new_user.is_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("sophie_bot.modules.greetings.middlewares.new_user.send_welcome", send_welcome)
    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.new_user.NewUserMiddleware.on_captcha", captcha_handler
    )

    with pytest.raises(SkipHandler):
        await middleware(handler, event, {"chat_db": chat_db, "new_users": new_users})

    send_welcome.assert_awaited_once()
    captcha_handler.assert_not_awaited()
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_muted_users_skips_enforcement_when_captcha_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="ok")
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(type="supergroup"),
    )

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.Message", SimpleNamespace)

    result = await middleware(
        handler,
        event,
        {
            "chat_db": SimpleNamespace(tid=-100123, iid=PydanticObjectId()),
            "user_db": SimpleNamespace(tid=123, iid=PydanticObjectId()),
        },
    )

    assert result == "ok"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_ban_unpassed_users_handle_skips_when_autokick_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = BanUnpassedUsers()
    user_iid = PydanticObjectId()
    group_iid = PydanticObjectId()
    user = SimpleNamespace(tid=123)
    group = SimpleNamespace(tid=-100123)
    ws_user = SimpleNamespace(
        id=PydanticObjectId(),
        passed=False,
        user=SimpleNamespace(ref=SimpleNamespace(id=user_iid)),
        group=SimpleNamespace(ref=SimpleNamespace(id=group_iid)),
        delete=AsyncMock(),
    )
    ban_user = AsyncMock()

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.schedules.ban_unpassed_users.ChatModel.get_by_iid",
        AsyncMock(side_effect=[user, group]),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.schedules.ban_unpassed_users.is_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.schedules.ban_unpassed_users.ban_user", ban_user)

    await schedule.process_user(ws_user)

    ban_user.assert_not_awaited()
    ws_user.delete.assert_not_awaited()
