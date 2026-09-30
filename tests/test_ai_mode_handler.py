from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, User

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.callbacks import AIModeCallback
from sophie_bot.modules.ai.handlers.aimode import AIModeSelectCallback


@pytest.mark.asyncio
async def test_mode_callback_answers_before_updating_mode_and_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline: list[str] = []

    async def answer(*args: object, **kwargs: object) -> None:
        if timeline:
            raise TelegramBadRequest(method=None, message="query is too old")  # type: ignore[arg-type]
        timeline.append("answer")

    message = Message.model_construct(
        chat=Chat(id=-100123, type="supergroup"),
        message_id=1,
    )
    callback = CallbackQuery.model_construct(
        from_user=User(id=42, is_bot=False, first_name="Admin"),
        message=message,
    )
    connection = SimpleNamespace(db_model=SimpleNamespace(iid="chat-iid"))

    async def set_mode(*args: object, **kwargs: object) -> None:
        timeline.append("set_mode")

    async def edit_reply_markup(*args: object, **kwargs: object) -> None:
        timeline.append("edit_reply_markup")

    monkeypatch.setattr(
        "sophie_bot.modules.ai.handlers.aimode.is_user_admin",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.handlers.aimode.get_chat_mode",
        AsyncMock(return_value=AIMode.disabled),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.handlers.aimode.set_chat_mode", set_mode)
    monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)

    handler = AIModeSelectCallback(
        callback,
        context=SimpleNamespace(connection=connection),
        services=SimpleNamespace(redis=object()),
        callback_data=AIModeCallback(mode=AIMode.support.value),
    )

    await handler.handle()

    assert timeline == ["answer", "set_mode", "edit_reply_markup"]
