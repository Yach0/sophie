"""While an AI answer is still being generated, the placeholder must stay a plain progress line.

The AI table header names what the model produced and how much quota is left — both are only known
once generation finished, so neither the header row nor the battery may appear before that.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from pydantic_ai.models import Model

from sophie_bot.modules.ai.utils.ai_header import AI_HEADER_LABEL, AI_HEADER_SEPARATOR
from sophie_bot.modules.ai.utils.chatbot_response import build_chatbot_header
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode, build_message_streamer

BATTERY_EMOJI = "🔋"


def _flags(**enabled: bool) -> Any:
    async def is_enabled(name: str, chat_tid: int | None = None) -> bool:
        del chat_tid
        return enabled.get(name, False)

    return is_enabled


async def _get_value(name: str, chat_tid: int | None = None) -> float:
    del name, chat_tid
    return 0.5


def _quota(remaining_credits: int = 500, total_credits: int = 1000) -> Any:
    async def get_quota_info(chat_iid: object) -> Any:
        del chat_iid
        return SimpleNamespace(remaining_credits=remaining_credits, total_credits=total_credits)

    return get_quota_info


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=7,
        message_thread_id=None,
        reply=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
    )


def _model() -> Model:
    return cast(Model, SimpleNamespace(model_name="gpt-5.5"))


def _assert_plain_progress(text: str) -> None:
    assert AI_HEADER_LABEL not in text
    assert AI_HEADER_SEPARATOR not in text
    assert BATTERY_EMOJI not in text


@pytest.mark.asyncio
async def test_thinking_placeholder_is_not_a_table_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.chatbot_streaming.is_enabled",
        _flags(ai_chatbot_thinking_message=True, ai_chatbot_streaming=True),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.get_value", _get_value)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", _quota())

    message = _message()
    streamer = await build_message_streamer(cast(Message, message), _model(), False)

    assert streamer is not None
    _assert_plain_progress(message.reply.await_args.args[0])


@pytest.mark.asyncio
async def test_streaming_placeholder_shows_no_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streaming without the thinking placeholder still must not spend a battery reading early."""
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.chatbot_streaming.is_enabled",
        _flags(ai_chatbot_streaming=True),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.get_value", _get_value)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", _quota())

    message = _message()
    streamer = await build_message_streamer(cast(Message, message), _model(), False)

    assert streamer is not None
    _assert_plain_progress(message.reply.await_args.args[0])


@pytest.mark.asyncio
async def test_progress_updates_stay_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", _quota())

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


@pytest.mark.asyncio
async def test_finished_reply_header_carries_the_table_and_the_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", _quota())

    header = await build_chatbot_header(cast(Any, "chat-iid"), _model(), [])

    text = header.to_html()
    assert AI_HEADER_LABEL in text
    assert AI_HEADER_SEPARATOR in text
    assert BATTERY_EMOJI in text
    assert "50%" in text
