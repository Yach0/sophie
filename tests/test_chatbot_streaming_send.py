from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import EditMessageText
from aiogram.types import Chat, Message
from fakeredis import FakeAsyncRedis
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from stfu_tg import Doc

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


@pytest.mark.parametrize("failure_stage", ["draft", "final"])
async def test_not_modified_does_not_abort_event_stream_or_final_reply(
    failure_stage: str, streaming_bot: Bot, test_redis: FakeAsyncRedis
) -> None:
    streamer = make_streamer(streaming_bot, test_redis)
    payloads: list[str] = []
    rejected = False

    async def edit_request(bot: Bot, method: EditMessageText, **kwargs: object) -> Message:
        nonlocal rejected
        assert method.rich_message is not None
        assert method.rich_message.html is not None
        payloads.append(method.rich_message.html)
        is_final = method.rich_message.html == "Final answer"
        if not rejected and is_final == (failure_stage == "final"):
            rejected = True
            raise TelegramBadRequest(method=method, message=MSG_NOT_MODIFIED)
        assert streamer.response_message is not None
        return streamer.response_message

    async def model_stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        yield "Answer"
        yield " continued"

    agent = Agent(FunctionModel(stream_function=model_stream))
    request = AsyncMock(side_effect=edit_request)
    with (
        patch.object(streaming_bot.session, "make_request", request),
        patch("sophie_bot.modules.ai.utils.ai_run._STREAM_DEBOUNCE_SECONDS", 0),
    ):
        async with chatbot_streaming_session(streamer):
            result = await run_ai_stream(agent, "Question", on_text_stream=streamer.stream)
            assert result.output == "Answer continued"
            response = await streamer.send_final(Doc("Final answer"))
            assert response is streamer.response_message
            assert streamer._last_sent_rich == "Final answer"

    assert rejected
    assert any("Answer continued" in payload for payload in payloads)
    assert payloads[-1] == "Final answer"
    assert streamer.final_message_sent


@pytest.mark.parametrize("failure_stage", ["draft", "final"])
@pytest.mark.parametrize(
    "error_type,error_message",
    [
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
