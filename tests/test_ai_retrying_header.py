from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from stfu_tg import Doc

from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode


def _build_streamer(response_message: SimpleNamespace) -> ChatbotMessageStreamer:
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, SimpleNamespace(chat=SimpleNamespace(id=-100123))),
        header=Doc("Initial"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=1,
    )
    streamer.response_message = cast(Message, response_message)
    return streamer


def _edited_text(response_message: SimpleNamespace) -> str:
    call_kwargs = response_message.edit_text.await_args.kwargs
    return call_kwargs.get("text") or response_message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_retrying_updates_chatbot_header() -> None:
    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = _build_streamer(response_message)

    await streamer.update_retrying(1, 5)

    response_message.edit_text.assert_awaited_once()
    assert "(Retrying 1/5...)" in _edited_text(response_message)


@pytest.mark.asyncio
async def test_header_update_keeps_already_streamed_text() -> None:
    """The agent loop can narrate, call a tool, then answer — a header-only edit would erase the
    narration the user is already reading."""
    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = _build_streamer(response_message)
    streamer.last_sent_text = "Let me check the docs."

    await streamer.update_retrying(1, 5)

    edited_text = _edited_text(response_message)
    assert "(Retrying 1/5...)" in edited_text
    assert "Let me check the docs." in edited_text


@pytest.mark.asyncio
async def test_stream_reasoning_shows_the_tail_of_the_models_reasoning() -> None:
    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = _build_streamer(response_message)
    streamer.throttle_seconds = 0

    await streamer.stream_reasoning("The user   is asking\nabout antiflood.")
    await streamer.stream_reasoning("   ")

    # Whitespace collapsed, and a blank update never costs an edit.
    response_message.edit_text.assert_awaited_once()
    assert "The user is asking about antiflood." in _edited_text(response_message)
