from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram_test_framework import MockBot, RequestCapture
from aiogram_test_framework.types import RequestType
from beanie import PydanticObjectId
from pydantic_ai.models.test import TestModel
from stfu_tg import Doc

from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils import ai_chatbot_reply as chatbot_reply
from sophie_bot.modules.ai.utils import ai_errors, ai_run, chatbot_context
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _send_chatbot_ai_failure_reply
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer
from sophie_bot.services.application import ApplicationServices

REPLY_KEYBOARD = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Exit AI help")]], resize_keyboard=True)
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


def _streamer(bot: MockBot, test_redis: object) -> ChatbotMessageStreamer:
    streamer = ChatbotMessageStreamer(
        source_message=_message(bot, 1),
        status=Doc("Header"),
        throttle_seconds=1,
        redis=test_redis,
    )
    streamer.response_message = _message(bot, 2)
    return streamer


@pytest.mark.asyncio
async def test_failure_reply_drops_reply_keyboard_on_edit(test_redis: object) -> None:
    bot = _bot()
    streamer = _streamer(bot, test_redis)

    await _send_chatbot_ai_failure_reply(
        _message(bot, 1), streamer, AIRequestFailed("AI request failed"), reply_markup=REPLY_KEYBOARD
    )

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.EDIT_MESSAGE_TEXT
    assert request.reply_markup is None


@pytest.mark.asyncio
async def test_failure_reply_keeps_inline_keyboard_on_edit(test_redis: object) -> None:
    bot = _bot()
    streamer = _streamer(bot, test_redis)

    await _send_chatbot_ai_failure_reply(
        _message(bot, 1), streamer, AIRequestFailed("AI request failed"), reply_markup=INLINE_KEYBOARD
    )

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.EDIT_MESSAGE_TEXT
    assert request.reply_markup is not None and "inline_keyboard" in request.reply_markup


@pytest.mark.asyncio
async def test_failure_reply_keeps_reply_keyboard_on_fresh_send() -> None:
    bot = _bot()

    await _send_chatbot_ai_failure_reply(
        _message(bot, 1), None, AIRequestFailed("AI request failed"), reply_markup=REPLY_KEYBOARD
    )

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.SEND_MESSAGE
    assert request.reply_markup is not None and "keyboard" in request.reply_markup


@pytest.mark.asyncio
async def test_streamer_final_drops_reply_keyboard_on_edit(test_redis: object) -> None:
    bot = _bot()
    streamer = _streamer(bot, test_redis)

    await streamer.send_final(Doc("Answer"), reply_markup=REPLY_KEYBOARD)

    request = bot.capture.all_requests[-1]
    assert request.request_type == RequestType.EDIT_MESSAGE_TEXT
    assert request.reply_markup is None


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("missing_model", ["primary", "fallback"])
async def test_missing_context_capacity_sends_reported_failure_before_model_request(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
    missing_model: str,
) -> None:
    bot = _bot()
    message = _message(bot, 1)
    connection = MagicMock(spec=ChatConnection)
    connection.tid = message.chat.id
    connection.db_model = MagicMock(iid=PydanticObjectId())
    primary = TestModel(model_name="unregistered/model")
    plan = AIModelPlan(
        candidates=(
            AIModelCandidate(
                model=primary,
                model_name=primary.model_name,
                context_window_tokens=None if missing_model == "primary" else 32768,
            ),
        )
    )
    monkeypatch.setattr(chatbot_reply, "_resolve_model_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(chatbot_reply, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(chatbot_context, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(chatbot_context, "get_value", AsyncMock(return_value=8192))
    monkeypatch.setattr(ai_run, "get_ai_model", lambda model_name: TestModel(model_name="fallback"))
    monkeypatch.setattr(
        ai_run, "registered_context_window_tokens", lambda model_name: None if missing_model == "fallback" else 16384
    )
    streamer = _streamer(bot, test_services.redis) if streaming else None
    monkeypatch.setattr(chatbot_reply, "build_message_streamer", AsyncMock(return_value=streamer))
    capture = MagicMock(return_value="context-error-reference")
    monkeypatch.setattr(ai_errors, "capture_ai_error", capture)
    build = AsyncMock()
    monkeypatch.setattr(chatbot_context.ModernContext, "build", build)
    legacy = MagicMock()
    monkeypatch.setattr(chatbot_context, "OldContext", legacy)
    run = AsyncMock()
    monkeypatch.setattr(chatbot_reply, "run_chatbot", run)

    await chatbot_reply.ai_chatbot_reply(message, connection, services=test_services, reply_markup=REPLY_KEYBOARD)

    request = bot.capture.all_requests[-1]
    assert request.request_type == (RequestType.EDIT_MESSAGE_TEXT if streaming else RequestType.SEND_MESSAGE)
    assert "context_window_tokens" in request.text
    assert "context-error-reference" in request.text
    assert (request.reply_markup is None) is streaming
    capture.assert_called_once()
    error, attribution = capture.call_args.args
    assert isinstance(error, ValueError)
    assert (primary.model_name if missing_model == "primary" else ai_run.AI_FALLBACK_MODEL_NAME) in str(error)
    assert attribution.operation == "context"
    build.assert_not_awaited()
    legacy.assert_not_called()
    run.assert_not_awaited()
