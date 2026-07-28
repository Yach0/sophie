from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram_test_framework import MockBot, RequestCapture
from aiogram_test_framework.types import RequestType
from stfu_tg import Doc

from sophie_bot.modules.ai.utils.ai_chatbot_reply import _send_chatbot_ai_failure_reply
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode

REPLY_KEYBOARD = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🛑 Exit AI help")]], resize_keyboard=True)
INLINE_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Help", callback_data="help")]])


def _bot() -> MockBot:
    return MockBot(
        capture=RequestCapture(),
        token="42:TEST",
        bot_id=42,
        bot_username="sophie_test_bot",
        bot_first_name="Sophie",
    )


def _message(bot: MockBot, message_id: int) -> Message:
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=483808054, type="private"),
    )
    return message.as_(cast(Any, bot))


def _streamer(bot: MockBot) -> ChatbotMessageStreamer:
    streamer = ChatbotMessageStreamer(
        source_message=_message(bot, 1),
        header=Doc("Header"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=1,
    )
    streamer.response_message = _message(bot, 2)
    return streamer


@pytest.mark.asyncio
async def test_failure_reply_drops_reply_keyboard_on_edit() -> None:
    """A private AI session sends a ReplyKeyboardMarkup, which editMessageText rejects with a
    pydantic ValidationError that no Telegram error handler can catch."""
    bot = _bot()
    streamer = _streamer(bot)

    await _send_chatbot_ai_failure_reply(
        _message(bot, 1), streamer, AIRequestFailed("AI request failed"), reply_markup=REPLY_KEYBOARD
    )

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.EDIT_MESSAGE_TEXT
    assert request.reply_markup is None


@pytest.mark.asyncio
async def test_failure_reply_keeps_inline_keyboard_on_edit() -> None:
    bot = _bot()
    streamer = _streamer(bot)

    await _send_chatbot_ai_failure_reply(
        _message(bot, 1), streamer, AIRequestFailed("AI request failed"), reply_markup=INLINE_KEYBOARD
    )

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.EDIT_MESSAGE_TEXT
    assert request.reply_markup is not None and "inline_keyboard" in request.reply_markup


@pytest.mark.asyncio
async def test_failure_reply_without_streamer_keeps_reply_keyboard() -> None:
    """A fresh send can carry the reply keyboard — only edits cannot."""
    bot = _bot()

    await _send_chatbot_ai_failure_reply(
        _message(bot, 1), None, AIRequestFailed("AI request failed"), reply_markup=REPLY_KEYBOARD
    )

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.SEND_MESSAGE
    assert request.reply_markup is not None and "keyboard" in request.reply_markup


@pytest.mark.asyncio
async def test_streamer_final_drops_reply_keyboard_on_edit() -> None:
    bot = _bot()
    streamer = _streamer(bot)

    await streamer.send_final(Doc("Answer"), reply_markup=REPLY_KEYBOARD)

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.EDIT_MESSAGE_TEXT
    assert request.reply_markup is None
