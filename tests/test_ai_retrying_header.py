from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_tool_update_flushes_the_latest_throttled_draft() -> None:
    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, SimpleNamespace(chat=SimpleNamespace(id=-100123))),
        header=Doc("Initial"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=60,
        tool_thinking_texts={"lookup": ("Looking it up...",)},
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.stream("Let me check")
    await streamer.stream("Let me check the docs.")
    await streamer.update_thinking_for_tool("lookup")

    assert response_message.edit_text.await_count == 2
    assert "Looking it up..." in _edited_text(response_message)
    assert "Let me check the docs." in _edited_text(response_message)


@pytest.mark.asyncio
async def test_throttled_draft_is_sent_after_the_backoff_expires() -> None:
    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, SimpleNamespace(chat=SimpleNamespace(id=-100123))),
        header=Doc("Initial"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=0.01,
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.stream("First draft")
    await streamer.stream("Latest draft")
    assert response_message.edit_text.await_count == 1

    await asyncio.sleep(0.02)

    assert response_message.edit_text.await_count == 2
    assert "Latest draft" in _edited_text(response_message)


@pytest.mark.asyncio
async def test_identical_rendered_tool_update_does_not_edit_telegram_twice() -> None:
    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, SimpleNamespace(chat=SimpleNamespace(id=-100123))),
        header=Doc("Initial"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=0,
        tool_thinking_texts={"lookup": ("Looking it up...",)},
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.update_thinking_for_tool("lookup")
    await streamer.update_thinking_for_tool("lookup")

    response_message.edit_text.assert_awaited_once()
