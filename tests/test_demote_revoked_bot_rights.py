from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import PromoteChatMember
from aiogram.types import User

from sophie_bot.modules.promotes.handlers.demote import DemoteUserHandler


@pytest.mark.parametrize("reason", [None, "CHAT_ADMIN_REQUIRED", "RIGHT_FORBIDDEN", "USER_NOT_FOUND"])
async def test_demote_reports_telegram_outcome(reason: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    promote = AsyncMock()
    reply = AsyncMock()
    handler = DemoteUserHandler(
        SimpleNamespace(
            from_user=User(id=123456, first_name="Operator", is_bot=False), reply_to_message=None, reply=reply
        ),
        user=User(id=654321, first_name="Target", is_bot=False),
        context=SimpleNamespace(connection=SimpleNamespace(tid=-100123, title="Group")),
        services=SimpleNamespace(bot=SimpleNamespace(promote_chat_member=promote)),
    )
    refresh = AsyncMock()
    confirmation = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.demote.get_admins_rights", refresh)
    monkeypatch.setattr("sophie_bot.modules.promotes.handlers.demote.reply_or_answer", confirmation)
    if reason is not None:
        promote.side_effect = TelegramBadRequest(
            method=PromoteChatMember(chat_id=-100123, user_id=654321), message=f"Bad Request: {reason}"
        )
    if reason == "USER_NOT_FOUND":
        with pytest.raises(TelegramBadRequest, match=reason):
            await handler.handle()
        reply.assert_not_awaited()
    else:
        await handler.handle()
    promote.assert_awaited_once()
    if reason is None:
        refresh.assert_awaited_once()
        confirmation.assert_awaited_once()
        reply.assert_not_awaited()
    else:
        refresh.assert_not_awaited()
        confirmation.assert_not_awaited()
        if reason != "USER_NOT_FOUND":
            reply.assert_awaited_once()
            assert "cannot demote" in reply.await_args.args[0].lower()
