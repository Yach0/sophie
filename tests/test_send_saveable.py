from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.enums import ContentType

from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.notes.utils import send as send_module


@pytest.mark.asyncio
async def test_send_saveable_forwards_message_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}

    class FakeSendMessage:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        def emit(self, bot: object) -> object:
            async def emit_result() -> object:
                return SimpleNamespace(message_id=42)

            return emit_result()

    monkeypatch.setitem(send_module.SEND_METHOD, ContentType.TEXT, FakeSendMessage)

    result = await send_module.send_saveable(
        message=None,
        send_to=-100123,
        saveable=Saveable(text="Threaded note", version=2),
        message_thread_id=987,
    )

    assert result is not None
    assert captured_kwargs["message_thread_id"] == 987
    assert captured_kwargs["reply_markup"].inline_keyboard == []
