from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import EditMessageText
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from fakeredis import FakeAsyncRedis
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from stfu_tg import Bold, Doc

from sophie_bot.modules.ai.utils.ai_run import run_ai_stream
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, chatbot_streaming_session
from sophie_bot.modules.utils_.telegram_exceptions import MSG_NOT_MODIFIED

pytestmark = pytest.mark.usefixtures("db_init")


@pytest.fixture
async def streaming_bot() -> AsyncIterator[Bot]:
    async with Bot("123456:TEST") as bot:
        yield bot


def make_streamer(bot: Bot, redis: FakeAsyncRedis) -> ChatbotMessageStreamer:
    source = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        text="Question",
    ).as_(bot)
    streamer = ChatbotMessageStreamer(source, "Thinking", 0, redis=redis)
    streamer.response_message = source.model_copy(update={"message_id": 3}).as_(bot)
    return streamer


async def test_repeated_stream_snapshots_and_final_render_do_not_edit(
    streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    assert streamer.response_message is not None
    streamer.response_message = streamer.response_message.model_copy(update={"text": None})
    request = AsyncMock(return_value=streamer.response_message)

    async def model_stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        yield "Answer"
        yield " continued"

    async def repeated_snapshot(text: str) -> None:
        await streamer.stream(text)
        await streamer.stream(text)

    agent = Agent(FunctionModel(stream_function=model_stream))
    with (
        patch.object(streaming_bot.session, "make_request", request),
        patch("sophie_bot.modules.ai.utils.ai_run._STREAM_DEBOUNCE_SECONDS", 0),
    ):
        async with chatbot_streaming_session(streamer):
            result = await run_ai_stream(agent, "Question", on_text_stream=repeated_snapshot)
            assert result.output == "Answer continued"
            before_final = request.await_count
            await streamer.send_final(await streamer._render_doc(result.output))
            assert request.await_count == before_final

    payloads = [call.args[1].rich_message.html for call in request.await_args_list]
    assert len(payloads) >= 2
    assert len(payloads) == len(set(payloads))
    assert "Answer continued" in payloads[-1]
    assert streamer.final_message_sent


async def test_rendered_content_comparison_preserves_formatting_changes(
    streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    request = AsyncMock(return_value=streamer.response_message)
    with patch.object(streaming_bot.session, "make_request", request):
        await streamer._update(Doc("Answer"))
        await streamer._update(Doc("Answer"))
        await streamer._update(Doc(Bold("Answer")))
        await streamer._update(Doc(Bold("Answer")))
    assert [call.args[1].rich_message.html for call in request.await_args_list] == ["Answer", "<b>Answer</b>"]


async def test_final_send_tracks_content_and_only_changed_keyboard_edits(
    streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    assert streamer.response_message is not None
    sent = streamer.response_message.model_copy(update={"text": None}).as_(streaming_bot)
    streamer.response_message = None
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="More", callback_data="more")]])
    request = AsyncMock(return_value=sent)
    with patch.object(streaming_bot.session, "make_request", request):
        assert await streamer.send_final(Doc("Answer"), reply_markup=keyboard) is sent
        assert streamer.response_message is sent
        assert streamer._last_sent_rich == "Answer"
        await streamer.send_final(Doc("Answer"), reply_markup=keyboard.model_copy(deep=True))
        assert request.await_count == 1
        changed_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Next", callback_data="next")]]
        )
        await streamer.send_final(Doc("Answer"), reply_markup=changed_keyboard)
        assert request.await_count == 2
        assert request.await_args.args[1].reply_markup == changed_keyboard
        await streamer.send_final(Doc("Answer"), reply_markup=changed_keyboard)
        assert request.await_count == 2
        await streamer.send_final(Doc("Answer"))
        assert request.await_count == 2
        await streamer.send_final(Doc("Answer"), reply_markup=None)
        assert request.await_count == 3
        assert request.await_args.args[1].reply_markup is None
        await streamer.send_final(Doc("Answer"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
        assert request.await_count == 3


@pytest.mark.parametrize("failure_stage", ["draft", "final"])
@pytest.mark.parametrize(
    "error_type,error_message",
    [
        (TelegramBadRequest, MSG_NOT_MODIFIED),
        (TelegramBadRequest, "message is too long"),
        (TelegramForbiddenError, "bot was blocked"),
        (TelegramNetworkError, "connection lost"),
    ],
)
async def test_real_edit_failures_still_propagate(
    failure_stage: str,
    error_type: type[TelegramBadRequest | TelegramForbiddenError | TelegramNetworkError],
    error_message: str,
    streaming_bot: Bot,
    test_redis: FakeAsyncRedis,
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    error = error_type(method=EditMessageText(chat_id=1, message_id=3, text="Answer"), message=error_message)
    with (
        patch.object(streaming_bot.session, "make_request", AsyncMock(side_effect=error)),
        pytest.raises(error_type) as raised,
    ):
        if failure_stage == "draft":
            await streamer.stream("Answer")
        else:
            await streamer.send_final(Doc("Final answer"))

    assert raised.value is error
    assert streamer._last_sent_rich is None
    assert not streamer.final_message_sent
    assert streamer.last_sent_text == ""
    request = AsyncMock(return_value=streamer.response_message)
    with patch.object(streaming_bot.session, "make_request", request):
        if failure_stage == "draft":
            await streamer.stream("Answer")
        else:
            await streamer.send_final(Doc("Final answer"))
    request.assert_awaited_once()
    assert streamer._last_sent_rich is not None


@pytest.mark.parametrize("stage", ["thinking", "draft", "final"])
async def test_failed_send_is_retryable(stage: str, streaming_bot: Bot, test_redis: FakeAsyncRedis) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    sent = streamer.response_message
    streamer.response_message = None
    error = TelegramNetworkError(method=EditMessageText(chat_id=1, message_id=3, text="Answer"), message="offline")
    request = AsyncMock(side_effect=[error, sent])
    with patch.object(streaming_bot.session, "make_request", request):
        with pytest.raises(TelegramNetworkError) as raised:
            if stage == "thinking":
                await streamer.send_thinking_message()
            elif stage == "draft":
                await streamer.stream("Answer")
            else:
                await streamer.send_final(Doc("Answer"))
        assert raised.value is error
        assert streamer.response_message is None
        assert streamer._last_sent_rich is None
        assert streamer.last_sent_text == ""
        assert not streamer.final_message_sent
        if stage == "thinking":
            await streamer.send_thinking_message()
        elif stage == "draft":
            await streamer.stream("Answer")
        else:
            await streamer.send_final(Doc("Answer"))
    assert request.await_count == 2
    assert streamer._last_sent_rich is not None


async def test_failed_markup_edit_preserves_last_successful_content(
    streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="More", callback_data="more")]])
    error = TelegramNetworkError(method=EditMessageText(chat_id=1, message_id=3, text="Answer"), message="offline")
    request = AsyncMock(return_value=streamer.response_message)
    with patch.object(streaming_bot.session, "make_request", request):
        await streamer._update(Doc("Answer"))
        request.side_effect = error
        with pytest.raises(TelegramNetworkError) as raised:
            await streamer.send_final(Doc("Answer"), reply_markup=keyboard)
        assert raised.value is error
        assert streamer._last_sent_rich == "Answer"
        assert not streamer.final_message_sent
        request.side_effect = None
        await streamer.send_final(Doc("Answer"), reply_markup=keyboard)
        assert request.await_count == 3
        await streamer.send_final(Doc("Answer"), reply_markup=keyboard)
        assert request.await_count == 3
