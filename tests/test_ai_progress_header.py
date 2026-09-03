"""While an AI answer is still being generated, the placeholder must stay a plain progress line.

The AI table header names what the model produced and how much quota is left — both are only known
once generation finished, so neither the header row nor the battery may appear before that.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from pydantic_ai.models import Model

from sophie_bot.modules.ai.utils.ai_header import (
    AI_HEADER_LABEL,
    AI_HEADER_SEPARATOR,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_progress import (
    AI_PROGRESS_CUSTOM_EMOJI_IDS,
    AI_PROGRESS_DEFAULT_CUSTOM_EMOJI_ID,
)
from sophie_bot.modules.ai.utils.chatbot_response import build_chatbot_header
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode, build_message_streamer

BATTERY_EMOJI = "🔋"
_RANDOM_EMOJI_ID = AI_PROGRESS_CUSTOM_EMOJI_IDS[-1]


class _IsEnabled(Protocol):
    async def __call__(self, name: str, chat_tid: int | None = None) -> bool: ...


def _flags(**enabled: bool) -> _IsEnabled:
    async def is_enabled(name: str, chat_tid: int | None = None) -> bool:
        del chat_tid
        return enabled.get(name, False)

    return is_enabled


async def _get_value(name: str, chat_tid: int | None = None) -> float:
    del name, chat_tid
    return 0.5


def _quota(remaining_credits: int = 500, total_credits: int = 1000) -> AsyncMock:
    return AsyncMock(return_value=SimpleNamespace(remaining_credits=remaining_credits, total_credits=total_credits))


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=7,
        message_thread_id=None,
        reply=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
    )


def _model() -> Model:
    return cast(Model, SimpleNamespace(model_name="gpt-5.5"))


def _custom_emoji_ids(html: str) -> list[str]:
    return re.findall(r'<tg-emoji emoji-id="(\d+)">', html)


def _assert_plain_progress(text: str) -> None:
    assert AI_HEADER_LABEL not in text
    assert AI_HEADER_SEPARATOR not in text
    assert BATTERY_EMOJI not in text


async def _streamer_with_flags(message: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, **flags: bool) -> Any:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.is_enabled", _flags(**flags))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.get_value", _get_value)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.chatbot_streaming.random_ai_progress_custom_emoji_id",
        lambda: _RANDOM_EMOJI_ID,
    )
    return await build_message_streamer(cast(Message, message), _model(), False)


@pytest.mark.asyncio
async def test_thinking_placeholder_is_not_a_table_row(monkeypatch: pytest.MonkeyPatch) -> None:
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    message = _message()
    streamer = await _streamer_with_flags(
        message, monkeypatch, ai_chatbot_thinking_message=True, ai_chatbot_streaming=True
    )

    assert streamer is not None
    _assert_plain_progress(message.reply.await_args.args[0])
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_placeholder_shows_no_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streaming without the thinking placeholder still must not spend a battery reading early."""
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    message = _message()
    streamer = await _streamer_with_flags(message, monkeypatch, ai_chatbot_streaming=True)

    assert streamer is not None
    _assert_plain_progress(message.reply.await_args.args[0])
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_updates_stay_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        header=cast(Any, "Initial"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=0,
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.update_retrying(1, 5)

    edited_text = response_message.edit_text.await_args.kwargs["text"]
    assert "(Retrying 1/5...)" in edited_text
    _assert_plain_progress(edited_text)
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_placeholder_emoji_stays_the_same_on_every_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the random-emoji flag the placeholder keeps one emoji instead of flickering."""
    message = _message()
    streamer = await _streamer_with_flags(
        message, monkeypatch, ai_chatbot_thinking_message=True, ai_chatbot_streaming=True
    )

    assert streamer is not None
    streamer.throttle_seconds = 0
    await streamer.update_retrying(1, 5)

    initial_emoji_ids = _custom_emoji_ids(message.reply.await_args.args[0])
    edited_emoji_ids = _custom_emoji_ids(streamer.response_message.edit_text.await_args.kwargs["text"])
    assert initial_emoji_ids == [AI_PROGRESS_DEFAULT_CUSTOM_EMOJI_ID]
    assert edited_emoji_ids == initial_emoji_ids


@pytest.mark.asyncio
async def test_random_emoji_flag_applies_without_the_thinking_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag picks the placeholder emoji; whether the thinking text is shown is a separate flag."""
    message = _message()
    streamer = await _streamer_with_flags(message, monkeypatch, ai_chatbot_streaming=True, ai_chatbot_random_emoji=True)

    assert streamer is not None
    streamer.throttle_seconds = 0
    await streamer.update_retrying(1, 5)

    assert _custom_emoji_ids(message.reply.await_args.args[0]) == [_RANDOM_EMOJI_ID]
    assert _custom_emoji_ids(streamer.response_message.edit_text.await_args.kwargs["text"]) == [_RANDOM_EMOJI_ID]


@pytest.mark.asyncio
async def test_finished_reply_header_carries_the_table_and_the_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", _quota())

    header = await build_chatbot_header(cast(Any, "chat-iid"), _model(), [])

    text = header.to_html()
    assert AI_HEADER_LABEL in text
    assert AI_HEADER_SEPARATOR in text
    assert BATTERY_EMOJI in text
    assert "50%" in text


def test_simple_header_is_inline_and_omits_table_status() -> None:
    header = build_ai_header("simple", "gpt-5.5", ai_credit_header(50))

    assert header is not None
    text = build_ai_message_doc("simple", header, "Hello").to_html()
    assert text.startswith("✨ Hello")
    assert "Hello" in text
    assert "50%" in text
    assert text.rfind("50%") > text.find("Hello")
    assert "gpt-5.5" not in text
    assert "\nHello" not in text


def test_disabled_header_leaves_only_the_body() -> None:
    header = build_ai_header("disable", "gpt-5.5", ai_credit_header(50))

    assert header is None
    assert build_ai_message_doc("disable", header, "Hello").to_html() == "Hello"
