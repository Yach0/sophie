from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from beanie import PydanticObjectId

from sophie_bot.modules.greetings.middlewares.new_user import NewUserMiddleware
from sophie_bot.modules.utils_.telegram_exceptions import REPLY_MESSAGE_INVALID

ADDER_TID = 111
JOINER_TID = 222
CHAT_TID = -100123
SERVICE_MESSAGE_ID = 77

MIDDLEWARE_PATH = "sophie_bot.modules.greetings.middlewares.new_user"


def _make_user(tid: int, first_name: str) -> SimpleNamespace:
    return SimpleNamespace(id=tid, is_bot=False, first_name=first_name, last_name=None, username=first_name.lower())


def _make_event(joiners: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        new_chat_members=joiners,
        from_user=_make_user(ADDER_TID, "Adder"),
        chat=SimpleNamespace(id=CHAT_TID, join_by_request=False, title="Group", username=None),
        date=None,
        message_id=SERVICE_MESSAGE_ID,
        message_thread_id=None,
    )


def _make_greetings(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "welcome_disabled": False,
        "welcome_security": None,
        "security_note": None,
        "note": SimpleNamespace(text="Hi {first}, welcome {mention}", file=None, files=None, buttons=[]),
        "clean_service": None,
        "clean_welcome": None,
        "welcome_mute": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_common(monkeypatch: pytest.MonkeyPatch, greetings: SimpleNamespace, *, is_admin: bool) -> None:
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.Message", SimpleNamespace)
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.GreetingsModel.get_by_chat_iid", AsyncMock(return_value=greetings))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.RulesModel.get_rules", AsyncMock(return_value=None))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.is_user_admin", AsyncMock(return_value=is_admin))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.is_enabled", AsyncMock(return_value=True))


@pytest.mark.asyncio
async def test_welcome_mute_restricts_the_new_member_not_the_adder(monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin adding a user must not get welcome-muted themselves; the joiner must."""
    joiner = _make_user(JOINER_TID, "Joiner")
    event = _make_event([joiner])
    chat_db = SimpleNamespace(iid=PydanticObjectId(), tid=CHAT_TID)
    new_users = [SimpleNamespace(iid=PydanticObjectId(), tid=JOINER_TID, is_bot=False)]
    greetings = _make_greetings(welcome_mute=SimpleNamespace(enabled=True, time="1h"))

    on_welcomemute = AsyncMock()
    _patch_common(monkeypatch, greetings, is_admin=True)
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.NewUserMiddleware.is_join_request", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.send_welcome", AsyncMock(return_value=None))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.on_welcomemute", on_welcomemute)

    with pytest.raises(SkipHandler):
        await NewUserMiddleware()(AsyncMock(), event, {"chat_db": chat_db, "new_users": new_users})

    on_welcomemute.assert_awaited_once_with(CHAT_TID, JOINER_TID, "1h")


@pytest.mark.asyncio
async def test_welcome_mute_restricts_every_joining_human(monkeypatch: pytest.MonkeyPatch) -> None:
    joiners = [_make_user(JOINER_TID, "Joiner"), _make_user(333, "Second"), _make_user(444, "SomeBot")]
    joiners[2].is_bot = True
    event = _make_event(joiners)
    chat_db = SimpleNamespace(iid=PydanticObjectId(), tid=CHAT_TID)
    new_users = [
        SimpleNamespace(iid=PydanticObjectId(), tid=JOINER_TID, is_bot=False),
        SimpleNamespace(iid=PydanticObjectId(), tid=333, is_bot=False),
        SimpleNamespace(iid=PydanticObjectId(), tid=444, is_bot=True),
    ]
    greetings = _make_greetings(welcome_mute=SimpleNamespace(enabled=True, time="1h"))

    on_welcomemute = AsyncMock()
    _patch_common(monkeypatch, greetings, is_admin=False)
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.NewUserMiddleware.is_join_request", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.send_welcome", AsyncMock(return_value=None))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.on_welcomemute", on_welcomemute)

    with pytest.raises(SkipHandler):
        await NewUserMiddleware()(AsyncMock(), event, {"chat_db": chat_db, "new_users": new_users})

    muted_tids = sorted(call.args[1] for call in on_welcomemute.await_args_list)
    assert muted_tids == [JOINER_TID, 333]


@pytest.mark.asyncio
async def test_welcome_fillings_resolve_to_the_joiner_not_the_adder(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_saveable must receive the joiner, otherwise it falls back to message.from_user (the adder)."""
    joiner = _make_user(JOINER_TID, "Joiner")
    event = _make_event([joiner])
    chat_db = SimpleNamespace(iid=PydanticObjectId(), tid=CHAT_TID)
    new_users = [SimpleNamespace(iid=PydanticObjectId(), tid=JOINER_TID, is_bot=False)]
    greetings = _make_greetings()

    send_saveable = AsyncMock(return_value=None)
    _patch_common(monkeypatch, greetings, is_admin=False)
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.NewUserMiddleware.is_join_request", AsyncMock(return_value=False))
    monkeypatch.setattr("sophie_bot.modules.greetings.utils.send_welcome.send_saveable", send_saveable)

    with pytest.raises(SkipHandler):
        await NewUserMiddleware()(AsyncMock(), event, {"chat_db": chat_db, "new_users": new_users})

    assert send_saveable.await_args.kwargs["user"] is joiner


@pytest.mark.asyncio
async def test_security_note_fillings_resolve_to_the_joiner(monkeypatch: pytest.MonkeyPatch) -> None:
    joiner = _make_user(JOINER_TID, "Joiner")
    event = _make_event([joiner])
    chat_db = SimpleNamespace(iid=PydanticObjectId(), tid=CHAT_TID)
    new_users = [SimpleNamespace(iid=PydanticObjectId(), tid=JOINER_TID, is_bot=False)]
    greetings = _make_greetings(
        welcome_security=SimpleNamespace(enabled=True),
        security_note=SimpleNamespace(text="Hi {first}, prove you are human", file=None, files=None, buttons=[]),
    )

    send_welcome = AsyncMock(return_value=None)
    _patch_common(monkeypatch, greetings, is_admin=False)
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.NewUserMiddleware.is_join_request", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.ws_on_new_users_mute", AsyncMock(return_value=[True]))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.send_welcome", send_welcome)

    with pytest.raises(SkipHandler):
        await NewUserMiddleware()(AsyncMock(), event, {"chat_db": chat_db, "new_users": new_users})

    assert send_welcome.await_args.kwargs["user"] is joiner


@pytest.mark.asyncio
async def test_join_request_joins_are_still_cleaned_up(monkeypatch: pytest.MonkeyPatch) -> None:
    joiner = _make_user(JOINER_TID, "Joiner")
    event = _make_event([joiner])
    chat_db = SimpleNamespace(iid=PydanticObjectId(), tid=CHAT_TID)
    new_users = [SimpleNamespace(iid=PydanticObjectId(), tid=JOINER_TID, is_bot=False)]
    greetings = _make_greetings(clean_service=SimpleNamespace(enabled=True))

    delete_messages = AsyncMock()
    handler = AsyncMock(return_value="handled")
    _patch_common(monkeypatch, greetings, is_admin=False)
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.NewUserMiddleware.is_join_request", AsyncMock(return_value=True))
    monkeypatch.setattr(f"{MIDDLEWARE_PATH}.bot", SimpleNamespace(delete_messages=delete_messages))

    result = await NewUserMiddleware()(handler, event, {"chat_db": chat_db, "new_users": new_users})

    assert result == "handled"
    handler.assert_awaited_once()
    delete_messages.assert_awaited_once_with(chat_id=CHAT_TID, message_ids=[SERVICE_MESSAGE_ID])


@pytest.mark.asyncio
async def test_self_welcome_sends_its_keyboard() -> None:
    message = SimpleNamespace(reply=AsyncMock(return_value=None), answer=AsyncMock(return_value=None))

    await NewUserMiddleware.self_welcome(message)

    markup = message.reply.await_args.kwargs["reply_markup"]
    assert [button.text for row in markup.inline_keyboard for button in row] == ["Documentation", "Support Chat"]


@pytest.mark.asyncio
async def test_self_welcome_keeps_its_keyboard_on_the_answer_fallback() -> None:
    reply = AsyncMock(side_effect=TelegramBadRequest(method=SimpleNamespace(), message=REPLY_MESSAGE_INVALID))
    message = SimpleNamespace(reply=reply, answer=AsyncMock(return_value=None))

    await NewUserMiddleware.self_welcome(message)

    markup = message.answer.await_args.kwargs["reply_markup"]
    assert [button.text for row in markup.inline_keyboard for button in row] == ["Documentation", "Support Chat"]
