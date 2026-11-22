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
from stfu_tg import Bold, Doc, Image

from sophie_bot.modules.ai.utils.ai_run import run_ai_stream
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, chatbot_streaming_session
from sophie_bot.modules.utils_.telegram_exceptions import MSG_NOT_MODIFIED

pytestmark = pytest.mark.usefixtures("db_init")

NOT_MODIFIED_DESCRIPTIONS = (
    MSG_NOT_MODIFIED,
    f"Bad Request: {MSG_NOT_MODIFIED}",
    (
        f"Bad Request: {MSG_NOT_MODIFIED}: specified new message content and reply markup are exactly the same "
        "as a current content and reply markup of the message"
    ),
)


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
        (TelegramBadRequest, "message was not modified"),
        (TelegramBadRequest, "message is not modifie"),
        (TelegramBadRequest, "message is not Modified"),
        (TelegramBadRequest, "MESSAGE_NOT_MODIFIED"),
        (TelegramBadRequest, "message is too long"),
        (TelegramForbiddenError, "bot was blocked"),
        (TelegramForbiddenError, MSG_NOT_MODIFIED),
        (TelegramNetworkError, "connection lost"),
        (TelegramNetworkError, MSG_NOT_MODIFIED),
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


@pytest.mark.parametrize("error_message", NOT_MODIFIED_DESCRIPTIONS)
async def test_draft_not_modified_preserves_message_and_allows_changed_edit(
    error_message: str, streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    original = streamer.response_message
    assert original is not None
    original_state = original.model_dump()
    error = TelegramBadRequest(method=EditMessageText(chat_id=1, message_id=3), message=error_message)
    request = AsyncMock(side_effect=[error, original])
    with patch.object(streaming_bot.session, "make_request", request):
        await streamer.stream("Answer")
        assert streamer.response_message is original
        assert streamer._last_sent_rich == (await streamer._render_doc("Answer")).to_rich()
        assert streamer.last_sent_text == "Answer"
        assert streamer.last_sent_at > 0
        await streamer.stream("Answer")
        request.assert_awaited_once()
        await streamer.stream("Answer continued")
    assert request.await_count == 2
    assert all(isinstance(call.args[1], EditMessageText) for call in request.await_args_list)
    assert "Answer continued" in request.await_args.args[1].rich_message.html
    assert streamer.response_message is original
    assert original.model_dump() == original_state


@pytest.mark.parametrize("photo_fallback", [False, True])
@pytest.mark.parametrize("error_message", NOT_MODIFIED_DESCRIPTIONS)
async def test_final_not_modified_completes_without_deleting_or_resending(
    photo_fallback: bool, error_message: str, streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    original = streamer.response_message
    assert original is not None
    original_state = original.model_dump()
    doc = Doc("Answer", Image("https://example.com/photo")) if photo_fallback else Doc("Answer")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="More", callback_data="more")]])
    no_op = TelegramBadRequest(method=EditMessageText(chat_id=1, message_id=3), message=error_message)
    outcomes = [no_op]
    if photo_fallback:
        outcomes.insert(
            0, TelegramBadRequest(method=no_op.method, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID")
        )
    request = AsyncMock(side_effect=outcomes)
    with patch.object(streaming_bot.session, "make_request", request):
        async with chatbot_streaming_session(streamer):
            assert await streamer.send_final(doc, reply_markup=keyboard) is original
            assert streamer.final_message_sent
            assert streamer._last_sent_rich == doc.to_rich()
            assert await streamer.send_final(doc, reply_markup=keyboard) is original
    assert request.await_count == len(outcomes)
    assert all(isinstance(call.args[1], EditMessageText) for call in request.await_args_list)
    assert streamer.response_message is original
    assert original.model_dump() == original_state
    if photo_fallback:
        assert "<img" not in request.await_args.args[1].rich_message.html


@pytest.mark.parametrize("stage", ["thinking", "draft", "final"])
@pytest.mark.parametrize("error_message", NOT_MODIFIED_DESCRIPTIONS)
async def test_not_modified_on_send_still_propagates(
    stage: str, error_message: str, streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    streamer.response_message = None
    error = TelegramBadRequest(method=EditMessageText(chat_id=1, message_id=3), message=error_message)
    request = AsyncMock(side_effect=error)
    with patch.object(streaming_bot.session, "make_request", request), pytest.raises(TelegramBadRequest) as raised:
        if stage == "thinking":
            await streamer.send_thinking_message()
        elif stage == "draft":
            await streamer.stream("Answer")
        else:
            await streamer.send_final(Doc("Answer"))
    assert raised.value is error
    request.assert_awaited_once()
    assert streamer.response_message is None
    assert streamer._last_sent_rich is None
    assert not streamer.final_message_sent


@pytest.mark.parametrize("error_message", NOT_MODIFIED_DESCRIPTIONS)
async def test_draft_photo_fallback_not_modified_allows_later_changed_edit(
    error_message: str, streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    original = streamer.response_message
    doc = Doc("Answer", Image("https://example.com/photo"))
    method = EditMessageText(chat_id=1, message_id=3)
    request = AsyncMock(
        side_effect=[
            TelegramBadRequest(method=method, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID"),
            TelegramBadRequest(method=method, message=error_message),
            original,
        ]
    )
    with patch.object(streaming_bot.session, "make_request", request):
        await streamer._update(doc)
        assert streamer._last_sent_rich == doc.to_rich()
        assert streamer.response_message is original
        await streamer._update(doc)
        assert request.await_count == 2
        await streamer._update(Doc("Changed answer"))
    assert request.await_count == 3
    assert all(isinstance(call.args[1], EditMessageText) for call in request.await_args_list)
    assert "<img" not in request.await_args_list[1].args[1].rich_message.html
    assert request.await_args.args[1].rich_message.html == "Changed answer"


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
