from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules.welcomesecurity.handlers.chat_join_request import ChatJoinRequestHandler
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import CaptchaDMBlockedError


@pytest.mark.asyncio
async def test_chat_join_request_sends_unblock_message_without_sending_join_request_saveable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=123456),
        date=datetime.now(UTC),
        approve=AsyncMock(),
    )
    connection = SimpleNamespace(db_model=SimpleNamespace(iid="connection_chat_iid"))
    chat = SimpleNamespace(iid="chat_iid", tid=-100123)
    user = SimpleNamespace(iid="user_iid", tid=123456)
    greetings = SimpleNamespace(
        welcome_security=SimpleNamespace(enabled=True),
        join_request_message=None,
        clean_welcome=None,
    )

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.ChatModel.get_by_tid",
        AsyncMock(side_effect=[chat, user]),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=greetings),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.ws_on_new_user",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.RulesModel.get_rules",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.initiate_captcha",
        AsyncMock(side_effect=CaptchaDMBlockedError()),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_enabled",
        AsyncMock(return_value=True),
    )

    send_saveable = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.handlers.chat_join_request.send_saveable", send_saveable)
    send_message = AsyncMock(return_value=SimpleNamespace(message_id=777))
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.bot",
        SimpleNamespace(send_message=send_message, delete_message=AsyncMock()),
    )
    aredis_set = AsyncMock()
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.aredis",
        SimpleNamespace(set=aredis_set),
    )

    handler = ChatJoinRequestHandler(event, connection=connection, state=SimpleNamespace())

    await handler.handle()

    send_saveable.assert_not_awaited()
    send_message.assert_awaited_once()
    assert aredis_set.await_count == 2
