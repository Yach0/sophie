from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules.welcomesecurity.handlers.chat_join_request import ChatJoinRequestHandler
from sophie_bot.modules.welcomesecurity.utils_.initiate_captcha import CaptchaDMBlockedError


def _join_request_handler(
    *, welcome_security_enabled: bool
) -> tuple[ChatJoinRequestHandler, AsyncMock, SimpleNamespace]:
    approve = AsyncMock()
    event = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=123456),
        date=datetime.now(UTC),
        approve=approve,
    )
    connection = SimpleNamespace(db_model=SimpleNamespace(iid="connection_chat_iid"))
    handler = ChatJoinRequestHandler(
        event,
        state=SimpleNamespace(),
        context=SimpleNamespace(connection=connection),
        dispatcher=object(),
        services=SimpleNamespace(bot=object(), redis=object()),
    )
    greetings = SimpleNamespace(
        welcome_security=SimpleNamespace(enabled=welcome_security_enabled),
    )
    return handler, approve, greetings


@pytest.mark.asyncio
async def test_whitelisted_join_request_stays_pending_when_welcome_security_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler, approve, greetings = _join_request_handler(welcome_security_enabled=False)
    chat = SimpleNamespace(iid="chat_iid", tid=-100123)
    is_whitelisted = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.ChatModel.get_by_tid",
        AsyncMock(return_value=chat),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=greetings),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_user_group_whitelisted",
        is_whitelisted,
    )

    await handler.handle()

    approve.assert_not_awaited()
    is_whitelisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_whitelisted_join_request_is_approved_when_captcha_enforcement_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler, approve, greetings = _join_request_handler(welcome_security_enabled=True)
    chat = SimpleNamespace(iid="chat_iid", tid=-100123)
    log_exemption = AsyncMock()

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.ChatModel.get_by_tid",
        AsyncMock(return_value=chat),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=greetings),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_enabled",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_user_group_whitelisted",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.log_group_whitelist_exemption",
        log_exemption,
    )

    await handler.handle()

    approve.assert_awaited_once()
    log_exemption.assert_awaited_once_with(-100123, 123456, "welcome_security_join_request_captcha")


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
        "sophie_bot.modules.welcomesecurity.handlers.chat_join_request.is_user_group_whitelisted",
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
    bot = SimpleNamespace(
        send_message=send_message,
        delete_message=AsyncMock(),
    )
    redis_set = AsyncMock()
    services = SimpleNamespace(
        bot=bot,
        redis=SimpleNamespace(set=redis_set),
    )

    handler = ChatJoinRequestHandler(
        event,
        state=SimpleNamespace(),
        context=SimpleNamespace(connection=connection),
        dispatcher=object(),
        services=services,
    )

    await handler.handle()

    send_saveable.assert_not_awaited()
    send_message.assert_awaited_once()
    assert redis_set.await_count == 2
