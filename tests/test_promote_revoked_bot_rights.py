"""Regression coverage for /promote when Telegram revokes the bot's rights."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import PromoteChatMember
from aiogram.types import User

from sophie_bot.config import CONFIG
from sophie_bot.modules.promotes.handlers.promote import PROMOTE_PERMISSIONS, PromoteUserHandler


def _handler() -> tuple[PromoteUserHandler, AsyncMock, AsyncMock, int]:
    invoker = User(id=123456, is_bot=False, first_name="Operator")
    target = User(id=654321, is_bot=False, first_name="Target")
    promote = AsyncMock()
    reply = AsyncMock()
    event = SimpleNamespace(from_user=invoker, reply_to_message=None, reply=reply)
    connection = SimpleNamespace(tid=-100123, title="Test group", db_model=SimpleNamespace())
    handler = PromoteUserHandler(
        event,
        user=target,
        admin_title=None,
        context=SimpleNamespace(connection=connection),
        services=SimpleNamespace(bot=SimpleNamespace(promote_chat_member=promote)),
    )
    return handler, promote, reply, target.id


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["CHAT_ADMIN_REQUIRED", "RIGHT_FORBIDDEN"])
async def test_promote_revoked_bot_rights_reports_failure(
    reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler, promote, reply, target_id = _handler()
    monkeypatch.setattr(CONFIG, "operators", [handler.event.from_user.id])
    cache_refresh = AsyncMock()
    confirmation = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.promote.get_admins_rights", cache_refresh)
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.promote.reply_or_answer", confirmation)
    promote.side_effect = TelegramBadRequest(
        method=PromoteChatMember(chat_id=-100123, user_id=target_id), message=reason
    )

    await handler.handle()

    promote.assert_awaited_once()
    reply.assert_awaited_once()
    assert "cannot promote" in reply.await_args.args[0].lower()
    cache_refresh.assert_not_awaited()
    confirmation.assert_not_awaited()


@pytest.mark.asyncio
async def test_promote_success_confirms_only_after_telegram_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler, promote, reply, target_id = _handler()
    monkeypatch.setattr(CONFIG, "operators", [handler.event.from_user.id])
    cache_refresh = AsyncMock()
    confirmation = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.promote.get_admins_rights", cache_refresh)
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.promote.reply_or_answer", confirmation)

    await handler.handle()

    promote.assert_awaited_once_with(
        chat_id=-100123,
        user_id=target_id,
        **{permission: True for permission in PROMOTE_PERMISSIONS},
    )
    cache_refresh.assert_awaited_once()
    confirmation.assert_awaited_once()
    assert "promoted successfully" in str(confirmation.await_args.args[1]).lower()
    reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_promote_unrelated_telegram_error_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, promote, reply, target_id = _handler()
    monkeypatch.setattr(CONFIG, "operators", [handler.event.from_user.id])
    confirmation = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.promote.reply_or_answer", confirmation)
    promote.side_effect = TelegramBadRequest(
        method=PromoteChatMember(chat_id=-100123, user_id=target_id), message="USER_NOT_FOUND"
    )

    with pytest.raises(TelegramBadRequest, match="USER_NOT_FOUND"):
        await handler.handle()

    reply.assert_not_awaited()
    confirmation.assert_not_awaited()
