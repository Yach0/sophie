from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Message
from pydantic_ai.models import Model
from stfu_tg import Doc
from stfu_tg.doc import Element

from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode


@pytest.mark.asyncio
async def test_retrying_updates_chatbot_header() -> None:
    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, SimpleNamespace(chat=SimpleNamespace(id=-100123))),
        header=Doc("Initial"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=1,
        connection=cast(ChatConnection, SimpleNamespace(db_model=SimpleNamespace(iid="chat-iid"))),
        model=cast(Model[Any], SimpleNamespace()),
    )
    streamer.response_message = cast(Message, response_message)

    async def fake_build_chatbot_header(*args: object, **kwargs: object) -> Element:
        additional_header_items = cast(list[Element], kwargs["additional_header_items"])
        return additional_header_items[0]

    with patch("sophie_bot.modules.ai.utils.chatbot_streaming.build_chatbot_header", fake_build_chatbot_header):
        await streamer.update_retrying(1, 5)

    response_message.edit_text.assert_awaited_once()
    call_kwargs = response_message.edit_text.await_args.kwargs
    edited_text = call_kwargs.get("text") or response_message.edit_text.await_args.args[0]
    assert "(Retrying 1/5...)" in edited_text
