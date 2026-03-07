from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from sophie_bot.modules.notes.handlers.list import NotesList


@pytest.mark.asyncio
async def test_notes_list_falls_back_to_send_message_when_reply_target_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(
        db_model=SimpleNamespace(iid="chat_iid"),
        tid=-1001483164428,
        title="🔥 Men Dont Fap 🔥",
    )
    reply_exception = TelegramBadRequest(
        method=SendMessage(chat_id=connection.tid, text="placeholder"),
        message="Bad Request: message to be replied not found",
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=connection.tid),
        message_thread_id=None,
        reply=AsyncMock(side_effect=reply_exception),
    )
    bot = SimpleNamespace(send_message=AsyncMock())

    monkeypatch.setattr("sophie_bot.modules.notes.handlers.list.NoteModel.get_chat_notes", AsyncMock(return_value=[]))

    handler = NotesList(
        message,
        bot=bot,
        connection=connection,
        search=None,
    )

    await handler.handle()

    assert message.reply.await_count == 1
    assert bot.send_message.await_count == 1

    send_call = bot.send_message.await_args
    assert send_call.kwargs["chat_id"] == connection.tid
    assert send_call.kwargs["message_thread_id"] is None
    assert "No notes found in" in send_call.kwargs["text"]
