from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from sophie_bot.modules.warns.handlers.warn import WarnHandler


@pytest.mark.asyncio
async def test_warn_handler_falls_back_to_send_message_when_reply_target_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_user = SimpleNamespace(iid="target_iid", tid=7860164386, first_name_or_title="Ajay")
    admin_user = SimpleNamespace(iid="admin_iid", tid=6023245799, first_name_or_title="Billy")
    chat_model = SimpleNamespace(iid="chat_iid")
    connection = SimpleNamespace(db_model=chat_model, tid=-1001483164428)
    warn_entry = SimpleNamespace(id="warn_iid")

    reply_exception = TelegramBadRequest(
        method=SendMessage(chat_id=connection.tid, text="placeholder"),
        message="Bad Request: message to be replied not found",
    )

    message = SimpleNamespace(
        chat=SimpleNamespace(id=connection.tid),
        from_user=SimpleNamespace(id=admin_user.tid, first_name="Billy"),
        reply_to_message=None,
        message_thread_id=6540626,
        reply=AsyncMock(side_effect=reply_exception),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    monkeypatch.setattr(
        "sophie_bot.modules.warns.handlers.warn.get_arg_or_reply_user",
        lambda current_message, data: target_user,
    )
    monkeypatch.setattr("sophie_bot.modules.warns.handlers.warn.is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "sophie_bot.modules.warns.handlers.warn.warn_user",
        AsyncMock(return_value=(1, 5, None, warn_entry)),
    )
    monkeypatch.setattr("sophie_bot.modules.warns.handlers.warn.log_event", AsyncMock())
    monkeypatch.setattr("sophie_bot.modules.warns.handlers.warn.RulesModel.get_rules", AsyncMock(return_value=None))

    handler = WarnHandler(
        message,
        bot=bot,
        connection=connection,
        user_db=admin_user,
        reason="no promotion allowed here read /rules",
    )

    await handler.handle()

    assert message.reply.await_count == 1
    assert bot.send_message.await_count == 1

    send_call = bot.send_message.await_args
    assert send_call.kwargs["chat_id"] == connection.tid
    assert send_call.kwargs["message_thread_id"] == message.message_thread_id
    assert "Warnings count" in send_call.kwargs["text"]
    assert send_call.kwargs["reply_markup"] is not None
