from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from stfu_tg import Bold, Doc, Image

from sophie_bot.modules.ai.handlers.research import ResearchProgressMessage
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message, send_ai_rich_message_to_chat
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer
from sophie_bot.modules.ai.utils.rich_delivery import with_photo_url_fallback


@pytest.mark.parametrize("path", ["reply", "chat", "final-edit", "draft-edit", "research-final"])
async def test_ai_photo_rejection_delivers_answer_with_links(path: str, test_redis: object) -> None:
    sent = Message(message_id=3, date=0, chat=Chat(id=-100123, type="supergroup"))
    error = TelegramBadRequest(method=None, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID")
    request = AsyncMock(side_effect=[error, sent])
    bot = SimpleNamespace(send_rich_message=request, edit_message_text=request)
    source = SimpleNamespace(chat=sent.chat, message_id=2, message_thread_id=9, bot=bot)
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Open", url="https://example.com")]])
    doc = Doc(Bold("The answer & context"), Image("https://example.com/photo?a=1&b=2", alt="Photo & details"))

    if path == "reply":
        result = await send_ai_rich_message(source, doc, reply_markup=markup)
    elif path == "chat":
        result = await send_ai_rich_message_to_chat(
            -100123, doc, reply_to_message_id=2, message_thread_id=9, reply_markup=markup, bot=bot
        )
    elif path == "research-final":
        result = await ResearchProgressMessage(sent, bot).send_final(doc)
    else:
        streamer = ChatbotMessageStreamer(source, "thinking", 0, redis=test_redis)
        streamer.response_message = sent.as_(bot)
        if path == "final-edit":
            result = await streamer.send_final(doc, reply_markup=markup)
            assert streamer.final_message_sent
        else:
            result = await streamer._update(doc)

    if path != "draft-edit":
        assert result is sent
    assert request.await_count == 2
    original, fallback = (call.kwargs for call in request.await_args_list)
    assert "<img " in original["rich_message"].html
    assert "<img" not in fallback["rich_message"].html
    assert '<a href="https://example.com/photo?a=1&amp;b=2">Photo &amp; details</a>' in fallback["rich_message"].html
    assert "The answer &amp; context" in fallback["rich_message"].html
    assert {key: value for key, value in original.items() if key != "rich_message"} == {
        key: value for key, value in fallback.items() if key != "rich_message"
    }


@pytest.mark.parametrize("photo_first", [False, True])
async def test_photo_fallback_composes_with_deleted_reply_target(photo_first: bool) -> None:
    photo_error = TelegramBadRequest(method=None, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID")
    reply_error = TelegramBadRequest(method=None, message="Bad Request: REPLY_MESSAGE_ID_INVALID")
    sent = SimpleNamespace(message_id=3)
    # A photo-first failure retries the rich reply, then removes the deleted reply target.
    outcomes = [photo_error, reply_error, sent] if photo_first else [reply_error, photo_error, reply_error, sent]
    request = AsyncMock(side_effect=outcomes)
    source = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=2,
        message_thread_id=9,
        bot=SimpleNamespace(send_rich_message=request),
    )
    assert await send_ai_rich_message(source, Doc("Answer", Image("https://example.com/photo"))) is sent
    assert request.await_count == len(outcomes)
    final = request.await_args.kwargs
    assert "reply_parameters" not in final
    assert final["message_thread_id"] == 9
    assert "<img" not in final["rich_message"].html


@pytest.mark.parametrize("reason", ["chat not found", "RICH_MESSAGE_PHOTO_URL_INVALID_OTHER"])
async def test_photo_delivery_propagates_unrelated_errors(reason: str) -> None:
    error = TelegramBadRequest(method=None, message=f"Bad Request: {reason}")
    request = AsyncMock(side_effect=error)
    with pytest.raises(TelegramBadRequest) as raised:
        await with_photo_url_fallback('<img src="https://example.com/photo"/>', request)
    assert raised.value is error
    request.assert_awaited_once()


async def test_photo_delivery_propagates_failed_retry() -> None:
    error = TelegramBadRequest(method=None, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID")
    request = AsyncMock(side_effect=error)
    with pytest.raises(TelegramBadRequest) as raised:
        await with_photo_url_fallback('<img src="https://example.com/photo"/>', request)
    assert raised.value is error
    assert request.await_count == 2


async def test_photo_delivery_without_images_does_not_retry() -> None:
    error = TelegramBadRequest(method=None, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID")
    request = AsyncMock(side_effect=error)
    with pytest.raises(TelegramBadRequest):
        await with_photo_url_fallback("Answer without images", request)
    request.assert_awaited_once()


@pytest.mark.parametrize("source", ["javascript:alert(1)", "file:///tmp/image", "https://[", "https://", "https://bad host/"])
async def test_photo_fallback_does_not_turn_unsafe_sources_into_links(source: str) -> None:
    error = TelegramBadRequest(method=None, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID")
    request = AsyncMock(side_effect=[error, True])
    await with_photo_url_fallback(f'<p>Answer &amp; context</p><img src="{source}" alt="Photo"/>', request)
    assert request.await_args.args[0].html == "<p>Answer &amp; context</p>Photo"


async def test_successful_photo_payload_is_not_changed() -> None:
    request = AsyncMock(return_value=True)
    rich_html = '<p>Answer</p><img src="https://example.com/photo"/>'
    assert await with_photo_url_fallback(rich_html, request)
    request.assert_awaited_once()
    assert request.await_args.args[0].html == rich_html


async def test_photo_fallback_preserves_other_rich_markup_verbatim() -> None:
    error = TelegramBadRequest(method=None, message="Bad Request: RICH_MESSAGE_PHOTO_URL_INVALID")
    request = AsyncMock(side_effect=[error, True])
    before = '<details open><summary>A &amp; B</summary>\n<table border="1"><tr><td>&#65;</td></tr></table>\n'
    after = '\n<tg-emoji emoji-id="123">✨</tg-emoji></details>'
    await with_photo_url_fallback(before + '<img src="https://example.com/photo" alt="Photo" tg-spoiler/>' + after, request)
    assert request.await_args.args[0].html == (
        before + '<tg-spoiler><a href="https://example.com/photo">Photo</a></tg-spoiler>' + after
    )
