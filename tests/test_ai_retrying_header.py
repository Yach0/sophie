from __future__ import annotations

import asyncio
from time import monotonic
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from stfu_tg import Doc

from sophie_bot.modules.ai.utils.ai_header import (
    AI_GENERATING_EMOJI_ID,
    AI_REASONING_EMOJI_ID,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer


def _response_message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=8,
        bot=SimpleNamespace(edit_message_text=AsyncMock()),
    )


def _build_streamer(response_message: SimpleNamespace) -> ChatbotMessageStreamer:
    streamer = ChatbotMessageStreamer(
        source_message=cast(
            Message,
            SimpleNamespace(chat=SimpleNamespace(id=-100123)),
        ),
        status=Doc("Initial"),
        throttle_seconds=1,
        redis=object(),
    )
    streamer.response_message = cast(Message, response_message)
    return streamer


def _edited_text(response_message: SimpleNamespace) -> str:
    return response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html


@pytest.mark.asyncio
async def test_retrying_updates_chatbot_header() -> None:
    response_message = _response_message()
    streamer = _build_streamer(response_message)

    await streamer.update_retrying(1, 5)

    response_message.bot.edit_message_text.assert_awaited_once()
    assert "(Retrying 1/5...)" in _edited_text(response_message)


@pytest.mark.asyncio
async def test_header_update_keeps_already_streamed_text() -> None:
    """The agent loop can narrate, call a tool, then answer — a header-only edit would erase the
    narration the user is already reading."""
    response_message = _response_message()
    streamer = _build_streamer(response_message)
    streamer.last_sent_text = "Let me check the docs."

    await streamer.update_retrying(1, 5)

    edited_text = _edited_text(response_message)
    assert "(Retrying 1/5...)" in edited_text
    assert "Let me check the docs." in edited_text


@pytest.mark.asyncio
async def test_stream_reasoning_shows_the_tail_of_the_models_reasoning() -> None:
    response_message = _response_message()
    streamer = _build_streamer(response_message)
    streamer.throttle_seconds = 0

    await streamer.stream_reasoning("The user   is asking\nabout antiflood.")
    await streamer.stream_reasoning("   ")

    # Whitespace collapsed, and a blank update never costs an edit.
    response_message.bot.edit_message_text.assert_awaited_once()
    assert _edited_text(response_message).startswith(
        f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji><br>'
    )
    assert (
        '<blockquote><tg-emoji emoji-id="5537353471893700616">💭</tg-emoji> '
        "<i>The user is asking about antiflood.</i></blockquote>"
    ) in _edited_text(response_message)


@pytest.mark.asyncio
async def test_reasoning_tail_keeps_four_hundred_characters() -> None:
    response_message = _response_message()
    streamer = _build_streamer(response_message)
    streamer.throttle_seconds = 0

    await streamer.stream_reasoning("first-" + "x" * 394)
    assert "first-" in _edited_text(response_message)

    await streamer.stream_reasoning("first-" + "x" * 400)
    html = _edited_text(response_message)
    assert f"<i>...{'x' * 400}</i>" in html
    assert "first-" not in html


@pytest.mark.asyncio
async def test_tool_status_keeps_reasoning_received_during_edit_backoff() -> None:
    response_message = _response_message()
    streamer = _build_streamer(response_message)
    streamer.throttle_seconds = 60
    streamer.last_sent_at = monotonic()

    await streamer.stream_reasoning("First five words and the rest of the reasoning.")
    response_message.bot.edit_message_text.assert_not_awaited()
    await streamer.update_thinking_for_tool("web_search")

    html = _edited_text(response_message)
    assert (
        f'<blockquote><tg-emoji emoji-id="{AI_REASONING_EMOJI_ID}">💭</tg-emoji> '
        "<i>First five words and the rest of the reasoning.</i></blockquote><br><i>"
    ) in html


@pytest.mark.asyncio
async def test_reasoning_markdown_and_tool_call_remain_visible_together(monkeypatch: pytest.MonkeyPatch) -> None:
    response_message = _response_message()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.choice", lambda texts: texts[0])
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, SimpleNamespace(chat=SimpleNamespace(id=-100123))),
        status="Thinking...",
        throttle_seconds=0,
        redis=object(),
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.stream("Let me check.")
    await streamer.stream_reasoning("I should **check docs** first.")
    await streamer.update_thinking_for_tool("web_search")

    html = _edited_text(response_message)
    assert html.count("Let me check.") == 1
    assert (
        f'<blockquote><tg-emoji emoji-id="{AI_REASONING_EMOJI_ID}">💭</tg-emoji> '
        "<i>I should <b>check docs</b> first.</i></blockquote><br><i>Searching the web...</i>"
    ) in html
    assert html.index("<b>check docs</b>") < html.index("Searching the web...") < html.index("5348210173104134595")
    assert "<i>Searching the web...</i><br>" in html
    assert "web_search" not in html
    assert "**" not in html


@pytest.mark.asyncio
async def test_tool_update_flushes_the_latest_throttled_draft() -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(
            Message,
            SimpleNamespace(chat=SimpleNamespace(id=-100123)),
        ),
        status=Doc("Initial"),
        throttle_seconds=60,
        redis=object(),
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.stream("Let me check")
    await streamer.stream("Let me check the docs.")
    await streamer.update_thinking_for_tool("lookup")

    assert response_message.bot.edit_message_text.await_count == 2
    assert "<i>Working on it...</i>" in _edited_text(response_message)
    assert "Let me check the docs." in _edited_text(response_message)


@pytest.mark.asyncio
async def test_throttled_draft_is_sent_after_the_backoff_expires() -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(
            Message,
            SimpleNamespace(chat=SimpleNamespace(id=-100123)),
        ),
        status=Doc("Initial"),
        throttle_seconds=0.01,
        redis=object(),
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.stream("First draft")
    await streamer.stream("Latest draft")
    assert response_message.bot.edit_message_text.await_count == 1

    await asyncio.sleep(0.02)

    assert response_message.bot.edit_message_text.await_count == 2
    assert "Latest draft" in _edited_text(response_message)


@pytest.mark.asyncio
async def test_identical_rendered_tool_update_does_not_edit_telegram_twice() -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(
            Message,
            SimpleNamespace(chat=SimpleNamespace(id=-100123)),
        ),
        status=Doc("Initial"),
        throttle_seconds=0,
        redis=object(),
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.update_thinking_for_tool("lookup")
    await streamer.update_thinking_for_tool("lookup")

    response_message.bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_tool_titles_survive_a_pending_draft_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    response_message = _response_message()
    streamer = _build_streamer(response_message)
    streamer.throttle_seconds = 0.01
    streamer.last_sent_at = monotonic()
    rendering = asyncio.Event()
    release_render = asyncio.Event()

    async def render_pending_draft(_text: str) -> Doc:
        rendering.set()
        await release_render.wait()
        return Doc("Draft progress")

    monkeypatch.setattr(streamer, "_render_doc", render_pending_draft)
    await streamer.stream("Draft")
    pending_update = streamer._pending_update_task
    assert pending_update is not None
    await asyncio.wait_for(rendering.wait(), timeout=1)

    final_doc = build_ai_message_doc(
        build_ai_header("simple", ai_credit_header(80)),
        "Answer",
        tool_labels=(AI_TOOLS_BY_NAME["web_search"],),
    )
    await streamer.send_final(final_doc)
    release_render.set()
    await asyncio.gather(pending_update, return_exceptions=True)

    assert _edited_text(response_message) == final_doc.to_rich()
    assert "(Search) Answer<br><p>" in _edited_text(response_message)
    assert 'emoji-id="5535248817659576336"' not in _edited_text(response_message)
