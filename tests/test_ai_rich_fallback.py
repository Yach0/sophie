from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from stfu_tg import Doc

from sophie_bot.modules.ai.handlers.research import ResearchProgressMessage
from sophie_bot.modules.ai.utils import ai_send
from sophie_bot.modules.ai.utils.ai_header import AI_GENERATING_EMOJI_ID, AI_PROGRESS_LINE_EMOJI_IDS
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer


class _RichFailure(Exception):
    pass


@pytest.mark.asyncio
async def test_chatbot_final_edit_propagates_telegram_failure(
    test_redis: object,
) -> None:
    source = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    error = _RichFailure()
    edit_message_text = AsyncMock(side_effect=error)
    response = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=3,
        bot=SimpleNamespace(edit_message_text=edit_message_text),
    )
    streamer = ChatbotMessageStreamer(
        source,
        "header",
        0,
        redis=test_redis,
    )
    streamer.response_message = response

    with pytest.raises(_RichFailure) as raised:
        await streamer.send_final(Doc("answer"))

    assert raised.value is error
    edit_message_text.assert_awaited_once()

@pytest.mark.usefixtures("db_init")
@pytest.mark.asyncio
async def test_chatbot_progress_edit_propagates_telegram_failure(test_redis: object) -> None:
    source = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    error = _RichFailure()
    edit_message_text = AsyncMock(side_effect=error)
    response = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=3,
        bot=SimpleNamespace(edit_message_text=edit_message_text),
    )
    streamer = ChatbotMessageStreamer(source, "header", 0, redis=test_redis)
    streamer.response_message = response

    with pytest.raises(_RichFailure) as raised:
        await streamer.stream("partial answer")

    assert raised.value is error
    edit_message_text.assert_awaited_once()

@pytest.mark.asyncio
async def test_rich_sender_uses_one_reply_on_success() -> None:
    sent = SimpleNamespace(message_id=3)
    send_rich_message = AsyncMock(return_value=sent)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=2,
        message_thread_id=4,
        bot=SimpleNamespace(send_rich_message=send_rich_message),
    )

    result = await ai_send.send_ai_rich_message(message, Doc("answer"))

    assert result is sent
    send_rich_message.assert_awaited_once()
    kwargs = send_rich_message.call_args.kwargs
    assert kwargs["rich_message"].html == Doc("answer").to_rich()
    assert kwargs["reply_parameters"].message_id == 2
    assert kwargs["message_thread_id"] == 4


@pytest.mark.asyncio
async def test_rich_sender_propagates_telegram_errors() -> None:
    error = _RichFailure()
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=2,
        message_thread_id=None,
        bot=SimpleNamespace(send_rich_message=AsyncMock(side_effect=error)),
    )

    with pytest.raises(_RichFailure):
        await ai_send.send_ai_rich_message(message, Doc("answer"))


@pytest.mark.asyncio
async def test_research_final_edit_propagates_telegram_failure() -> None:
    error = _RichFailure()
    edit_message_text = AsyncMock(side_effect=error)
    bot = SimpleNamespace(edit_message_text=edit_message_text)
    progress = ResearchProgressMessage(SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2), bot)

    with pytest.raises(_RichFailure) as raised:
        await progress.send_final(Doc("answer"))

    assert raised.value is error
    edit_message_text.assert_awaited_once()
    payload = edit_message_text.call_args.kwargs["rich_message"].html
    assert payload == Doc("answer").to_rich()


@pytest.mark.asyncio
async def test_research_progress_edit_propagates_telegram_failure() -> None:
    error = _RichFailure()
    edit_message_text = AsyncMock(side_effect=error)
    progress = ResearchProgressMessage(
        SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2),
        SimpleNamespace(edit_message_text=edit_message_text),
    )

    with pytest.raises(_RichFailure) as raised:
        await progress.update("searching")

    assert raised.value is error
    edit_message_text.assert_awaited_once()
    payload = edit_message_text.call_args.kwargs["rich_message"].html
    assert AI_GENERATING_EMOJI_ID in payload
    assert all(emoji_id in payload for emoji_id in AI_PROGRESS_LINE_EMOJI_IDS)


@pytest.mark.asyncio
async def test_send_ai_rich_message_retries_without_deleted_reply_target() -> None:
    sent = SimpleNamespace(message_id=201)
    error = TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")  # type: ignore[arg-type]
    send_rich_message = AsyncMock(side_effect=[error, sent])
    source = SimpleNamespace(
        chat=SimpleNamespace(id=100),
        message_id=200,
        message_thread_id=5,
        bot=SimpleNamespace(send_rich_message=send_rich_message),
    )

    result = await ai_send.send_ai_rich_message(source, Doc("answer"), reply_markup=None)

    assert result is sent
    assert send_rich_message.await_count == 2
    first, second = (call.kwargs for call in send_rich_message.await_args_list)
    assert first["reply_parameters"].message_id == 200
    assert "reply_parameters" not in second
    for kwargs in (first, second):
        assert kwargs["chat_id"] == 100
        assert kwargs["message_thread_id"] == 5
        assert kwargs["rich_message"].html == Doc("answer").to_rich()
        assert kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_send_ai_rich_message_propagates_unrelated_bad_request() -> None:
    error = TelegramBadRequest(method=None, message="Bad Request: chat not found")  # type: ignore[arg-type]
    send_rich_message = AsyncMock(side_effect=error)
    source = SimpleNamespace(
        chat=SimpleNamespace(id=100),
        message_id=200,
        message_thread_id=5,
        bot=SimpleNamespace(send_rich_message=send_rich_message),
    )

    with pytest.raises(TelegramBadRequest) as raised:
        await ai_send.send_ai_rich_message(source, Doc("answer"))

    assert raised.value is error
    send_rich_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleted_source_still_gets_thinking_message_and_final_edit(test_redis: object) -> None:
    error = TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")  # type: ignore[arg-type]
    edit_message_text = AsyncMock()
    bot = SimpleNamespace(edit_message_text=edit_message_text)
    sent = SimpleNamespace(chat=SimpleNamespace(id=100), message_id=201, bot=bot)
    bot.send_rich_message = AsyncMock(side_effect=[error, sent])
    source = SimpleNamespace(chat=SimpleNamespace(id=100), message_id=200, message_thread_id=5, bot=bot)
    streamer = ChatbotMessageStreamer(source, "thinking", 0, redis=test_redis)

    await streamer.send_thinking_message()
    result = await streamer.send_final(Doc("answer"))

    assert streamer.response_message is sent
    assert result is sent
    assert bot.send_rich_message.await_count == 2
    assert "reply_parameters" not in bot.send_rich_message.await_args_list[1].kwargs
    edit_message_text.assert_awaited_once()
    assert edit_message_text.await_args.kwargs["rich_message"].html == Doc("answer").to_rich()




@pytest.mark.asyncio
async def test_send_ai_rich_message_to_chat_normalizes_reply_to_message_id() -> None:
    send_rich_mock = AsyncMock(return_value=SimpleNamespace(message_id=42))
    bot = SimpleNamespace(send_rich_message=send_rich_mock)

    await ai_send.send_ai_rich_message_to_chat(
        chat_id=12345,
        doc=Doc("Hello rich"),
        reply_to_message_id=999,
        message_thread_id=10,
        bot=bot,
    )

    send_rich_mock.assert_awaited_once()
    kwargs = send_rich_mock.call_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert kwargs["message_thread_id"] == 10
    assert kwargs["reply_parameters"].message_id == 999
