from __future__ import annotations

import re
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage, SendRichMessage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    RichBlockParagraph,
    RichMessage,
    RichTextCustomEmoji,
    User,
)
from fakeredis import FakeAsyncRedis
from stfu_tg import BlockQuote, Bold, Button, Buttons, Doc, Image, PreformattedHTML, Section, Url

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.handlers.reply import AiReplyHandler
from sophie_bot.modules.ai.handlers.research import ResearchProgressMessage
from sophie_bot.modules.ai.handlers.translate import _build_translate_reply_doc
from sophie_bot.modules.ai.json_schemas.translate import AITranslateResponseSchema
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _build_chatbot_reply_doc, _reply_debug_history
from sophie_bot.modules.ai.utils.ai_header import build_ai_message_doc
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message, send_ai_rich_message_to_chat
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer
from sophie_bot.modules.ai.utils.self_reply import cut_titlebar
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.delivery import deliver_doc
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.modules.utils_.reply_or_edit import reply_or_edit, reply_or_edit_rich
from sophie_bot.utils.i18n import LazyProxy

type DeliveryMethod = SendMessage | SendRichMessage | EditMessageText | DeleteMessage
type Transport = tuple[Bot, list[DeliveryMethod]]


class VisibleText(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.feed(source)
        self.close()

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    @property
    def text(self) -> str:
        return "".join(self.parts)


def source_message(bot: Bot, message_id: int = 20) -> Message:
    return Message.model_validate(
        {
            "message_id": message_id,
            "date": 0,
            "chat": {"id": -100123, "type": "supergroup"},
            "message_thread_id": 7,
            "is_topic_message": True,
            "text": "question",
        }
    ).as_(bot)


def callback(message: Message) -> CallbackQuery:
    return CallbackQuery.model_validate(
        {
            "id": "callback",
            "from": {"id": 1, "is_bot": False, "first_name": "User"},
            "chat_instance": "instance",
            "message": message.model_dump(),
            "data": "test",
        }
    ).as_(message.bot)


@pytest.fixture
def transport() -> Transport:
    calls: list[DeliveryMethod] = []
    next_message_id = 100
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyz0123456789")

    async def deliver(owner: Bot, method: DeliveryMethod, **kwargs: object) -> Message | bool:
        nonlocal next_message_id
        if isinstance(method, DeleteMessage):
            calls.append(method)
            return True
        payload = (
            method.rich_message.html
            if isinstance(method, SendRichMessage) or (isinstance(method, EditMessageText) and method.rich_message)
            else method.text
        )
        assert payload is not None
        assert len(VisibleText(payload).text.encode("utf-16-le")) // 2 <= 4096
        calls.append(method)
        if isinstance(method, EditMessageText):
            return source_message(owner, method.message_id)
        next_message_id += 1
        return source_message(owner, next_message_id)

    bot.session = AsyncMock(side_effect=deliver)
    return bot, calls


def payloads(calls: list[DeliveryMethod]) -> list[str]:
    return [
        method.rich_message.html
        if isinstance(method, SendRichMessage) or (isinstance(method, EditMessageText) and method.rich_message)
        else method.text
        for method in calls
    ]


def assert_complete(calls: list[DeliveryMethod], expected: str) -> None:
    assert len(calls) > 1
    visible_chunks = [VisibleText(payload).text for payload in payloads(calls)]
    for index, payload in enumerate(payloads(calls)[1:], start=1):
        marker = re.match(r'<tg-emoji emoji-id="\d+">([^<]+)</tg-emoji> ', payload)
        if marker:
            visible_chunks[index] = visible_chunks[index][len(marker[1]) + 1 :]
    assert "".join(visible_chunks) == expected
    for method in calls:
        assert method.chat_id == -100123
        if not isinstance(method, EditMessageText):
            assert method.message_thread_id == 7
            assert method.reply_parameters.message_id == 20


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("rich", [False, True])
async def test_shared_reply_delivery_preserves_astral_text_links_and_keyboard(transport: Transport, rich: bool) -> None:
    bot, calls = transport
    body = "🌍 &<> " * 1400
    doc = Doc(Bold(Url(body, "https://example.org/" + "long" * 2000)))
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Action", callback_data="action")]])
    if rich:
        result = await reply_or_edit_rich(source_message(bot), doc, bot=bot, reply_markup=markup)
    else:
        result = await reply_or_answer(source_message(bot), doc, reply_markup=markup)
    assert result.message_id == 101
    assert_complete(calls, VisibleText(doc.to_rich() if rich else doc.to_html()).text)
    assert calls[0].reply_markup == markup
    assert all(method.reply_markup is None for method in calls[1:])
    assert all("<b>" in payload and "https://example.org/" in payload for payload in payloads(calls))


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("rich", [False, True])
async def test_callback_edits_first_then_sends_ordered_continuations(transport: Transport, rich: bool) -> None:
    bot, calls = transport
    doc = Doc(Bold("🌍" * 5000))
    if rich:
        await reply_or_edit_rich(callback(source_message(bot)), doc, bot=bot)
    else:
        await reply_or_edit(callback(source_message(bot)), doc)
    assert isinstance(calls[0], EditMessageText)
    assert calls[0].message_id == 20
    assert_complete(calls, VisibleText(doc.to_rich() if rich else doc.to_html()).text)


@pytest.mark.asyncio(loop_scope="session")
async def test_translation_preserves_long_text_and_notes(transport: Transport) -> None:
    bot, calls = transport
    doc = _build_translate_reply_doc(
        AITranslateResponseSchema(
            needs_translation=True,
            origin_language_name="English",
            origin_language_emoji="🇬🇧",
            translated_text="**🌍 translated** " * 1200,
            translation_explanations="Notes " * 1000,
        ),
        "German",
        False,
        False,
        None,
    )
    await send_ai_rich_message(source_message(bot), doc)
    assert_complete(calls, VisibleText(doc.to_rich()).text)
    assert "Translation Notes" in "".join(payloads(calls))


@pytest.mark.asyncio(loop_scope="session")
async def test_proactive_delivery_preserves_nested_rich_layout(transport: Transport) -> None:
    bot, calls = transport
    doc = Doc(
        PreformattedHTML(
            "<details><summary>Summary</summary><table><tr><td><b>"
            + "🌍 nested " * 1200
            + "</b></td></tr></table></details>"
        )
    )
    await send_ai_rich_message_to_chat(-100123, doc, reply_to_message_id=20, message_thread_id=7, bot=bot)
    assert_complete(calls, VisibleText(doc.to_rich()).text)
    assert sum(payload.count("Summary") for payload in payloads(calls)) == 1
    assert all("<details>" in payload and "<table>" in payload for payload in payloads(calls)[1:])


@pytest.mark.asyncio(loop_scope="session")
async def test_chatbot_placeholder_final_edit_preserves_full_output(
    transport: Transport, test_redis: FakeAsyncRedis
) -> None:
    bot, calls = transport
    streamer = ChatbotMessageStreamer(source_message(bot), "thinking", 0, redis=test_redis)
    await streamer.send_thinking_message()
    calls.clear()
    doc = Doc(Bold("🌍 full answer " * 1200))
    result = await streamer.send_final(doc)
    assert result.message_id == 101
    assert isinstance(calls[0], EditMessageText)
    assert calls[0].message_id == 101
    assert_complete(calls, VisibleText(doc.to_rich()).text)


@pytest.mark.asyncio(loop_scope="session")
async def test_chatbot_document_builder_does_not_truncate(test_redis: FakeAsyncRedis) -> None:
    text = "Full answer " * 1200
    await test_redis.hset(
        "sophie:kill_switch_chat:-100123",
        mapping={
            "ai_chatbot_table_max_columns": "6",
            "ai_chatbot_table_max_rows": "20",
            "ai_chatbot_table_card_threshold": "4",
        },
    )
    doc = await _build_chatbot_reply_doc(
        None, text, None, None, False, -100123, services=SimpleNamespace(redis=test_redis)
    )
    assert text in VisibleText(doc.to_rich()).text


@pytest.mark.asyncio(loop_scope="session")
async def test_research_placeholder_final_edit_preserves_full_output(transport: Transport) -> None:
    bot, calls = transport
    source = source_message(bot)
    progress = await ResearchProgressMessage.send(source)
    calls.clear()
    doc = Doc(Bold("🌍 research " * 1200))
    await progress.send_final(doc)
    assert isinstance(calls[0], EditMessageText)
    assert calls[0].message_id == 101
    assert_complete(calls, VisibleText(doc.to_rich()).text)


@pytest.mark.asyncio(loop_scope="session")
async def test_debug_history_preserves_complete_history(transport: Transport) -> None:
    bot, calls = transport
    history = "🌍 <debug> & history\n" * 1200
    await _reply_debug_history(source_message(bot), SimpleNamespace(history_debug=lambda: Doc(Bold(history))))
    expected = Section(BlockQuote(Doc(Bold(history)), expandable=True), title="LLM History").to_html()
    assert_complete(calls, VisibleText(expected).text)
    assert all("<blockquote" in payload for payload in payloads(calls)[1:])


@pytest.mark.asyncio(loop_scope="session")
async def test_shared_ordinary_delivery_propagates_too_long_errors() -> None:
    error = TelegramBadRequest(method=SendMessage(chat_id=1, text="test"), message="Bad Request: message is too long")
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyz0123456789")
    bot.session = AsyncMock(side_effect=error)
    with pytest.raises(TelegramBadRequest) as raised:
        await reply_or_answer(source_message(bot), Doc("short"))
    assert raised.value is error


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("markup", ['<img src="file"/>', "<tg-buttons>action</tg-buttons>"])
async def test_unsupported_rich_media_and_buttons_fail_before_delivery(transport: Transport, markup: str) -> None:
    bot, calls = transport
    with pytest.raises(ValueError):
        await send_ai_rich_message(source_message(bot), Doc("text" * 1200, PreformattedHTML(markup)))
    assert calls == []


@pytest.mark.asyncio(loop_scope="session")
async def test_legacy_delivery_does_not_silently_drop_oversized_surfaces() -> None:
    error = TelegramBadRequest(method=SendMessage(chat_id=1, text="test"), message="Bad Request: message is too long")
    send = AsyncMock(side_effect=error)
    with pytest.raises(TelegramBadRequest) as raised:
        await common_try(send())
    assert raised.value is error


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("kwargs", [{"caption": "caption"}, {"entities": []}, {"parse_mode": "MarkdownV2"}])
async def test_unsupported_ordinary_surfaces_fail_before_delivery(
    transport: Transport, kwargs: dict[str, object]
) -> None:
    bot, calls = transport
    with pytest.raises(ValueError):
        await reply_or_answer(source_message(bot), "text" * 1200, **kwargs)
    assert calls == []


@pytest.mark.asyncio(loop_scope="session")
async def test_plain_text_keeps_literal_markup_and_entities(transport: Transport) -> None:
    bot, calls = transport
    text = "🌍 <b>literal</b> &amp; " * 1000
    await reply_or_answer(source_message(bot), text, parse_mode=None)
    assert_complete(calls, text)


@pytest.mark.asyncio(loop_scope="session")
async def test_continuation_failure_stops_delivery_and_propagates(transport: Transport) -> None:
    bot, calls = transport
    send = bot.session.side_effect
    error = TelegramBadRequest(method=SendMessage(chat_id=1, text="test"), message="Bad Request: chat not found")

    async def fail_second(
        owner: Bot, method: SendMessage | SendRichMessage | EditMessageText, **kwargs: object
    ) -> Message:
        if calls:
            raise error
        return await send(owner, method, **kwargs)

    bot.session.side_effect = fail_second
    with pytest.raises(TelegramBadRequest) as raised:
        await send_ai_rich_message(source_message(bot), Doc("🌍" * 10000))
    assert raised.value is error
    assert len(calls) == 1
    assert bot.session.await_count == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_deleted_reply_retry_keeps_topic_and_complete_content(transport: Transport) -> None:
    bot, calls = transport
    send = bot.session.side_effect
    error = TelegramBadRequest(
        method=SendMessage(chat_id=1, text="test"), message="Bad Request: message to be replied not found"
    )

    async def deleted_reply(
        owner: Bot, method: SendMessage | SendRichMessage | EditMessageText, **kwargs: object
    ) -> Message:
        if method.reply_parameters is not None:
            raise error
        return await send(owner, method, **kwargs)

    bot.session.side_effect = deleted_reply
    text = "🌍 full content " * 1000
    result = await send_ai_rich_message(source_message(bot), Doc(Bold(text)))
    assert result.message_id == 101
    assert len(calls) > 1
    assert "".join(VisibleText(payload).text for payload in payloads(calls)) == text
    assert all(method.message_thread_id == 7 and method.reply_parameters is None for method in calls)
    assert bot.session.await_count == len(calls) + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_oversized_stream_draft_edits_one_page_then_final_delivers_everything(
    transport: Transport, test_redis: FakeAsyncRedis
) -> None:
    bot, calls = transport
    await test_redis.hset(
        "sophie:kill_switch_chat:-100123",
        mapping={
            "ai_chatbot_table_max_columns": "6",
            "ai_chatbot_table_max_rows": "20",
            "ai_chatbot_table_card_threshold": "4",
        },
    )
    streamer = ChatbotMessageStreamer(source_message(bot), "thinking", 0, redis=test_redis)
    await streamer.send_thinking_message()
    calls.clear()
    text = "🌍 full output " * 1200
    await streamer.stream(text)
    assert len(calls) == 1
    assert isinstance(calls[0], EditMessageText)
    calls.clear()
    doc = Doc(Bold(text))
    await streamer.send_final(doc)
    assert isinstance(calls[0], EditMessageText)
    assert_complete(calls, text)
    count = len(calls)
    await streamer.send_final(doc)
    assert len(calls) == count
    await streamer.stop()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("rich", [False, True])
async def test_callback_continuations_preserve_existing_reply_context(transport: Transport, rich: bool) -> None:
    bot, calls = transport
    original = source_message(bot, 19)
    target = source_message(bot).model_copy(update={"reply_to_message": original})
    doc = Doc("🌍" * 5000)
    if rich:
        await reply_or_edit_rich(callback(target), doc, bot=bot)
    else:
        await reply_or_edit(callback(target), doc)
    assert isinstance(calls[0], EditMessageText)
    assert len(calls) > 1
    assert all(method.reply_parameters.message_id == 19 for method in calls[1:])


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("rich", [False, True])
@pytest.mark.parametrize("doc", [Doc(Image("photo")), Doc(Buttons(Button("action", url="https://example.org")))])
async def test_native_media_and_buttons_have_explicit_text_boundary_errors(
    transport: Transport, rich: bool, doc: Doc
) -> None:
    bot, calls = transport
    with pytest.raises(ValueError):
        if rich:
            await send_ai_rich_message(source_message(bot), doc)
        else:
            await reply_or_answer(source_message(bot), doc)
    assert calls == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize(
    "button",
    [Button("action", url="https://example.org/" + "path" * 1500), Button("action", callback_data="action:1")],
)
async def test_short_rich_buttons_keep_existing_reply_or_edit_path(
    transport: Transport, edit: bool, button: Button
) -> None:
    bot, calls = transport
    doc = Doc("body", Buttons(button))
    event = callback(source_message(bot)) if edit else source_message(bot)
    await reply_or_edit_rich(event, doc, bot=bot)
    assert len(calls) == 1
    assert payloads(calls) == [doc.to_rich()]
    assert isinstance(calls[0], EditMessageText if edit else SendRichMessage)


@pytest.mark.asyncio(loop_scope="session")
async def test_oversized_rich_buttons_rejected_before_reply_or_edit(transport: Transport) -> None:
    bot, calls = transport
    doc = Doc("🌍" * 3000, Buttons(Button("action", url="https://example.org")))
    with pytest.raises(ValueError):
        await reply_or_edit_rich(source_message(bot), doc, bot=bot)
    assert calls == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("rich", [False, True])
@pytest.mark.parametrize("long", [False, True])
async def test_callback_unchanged_first_page_still_delivers_continuations(
    transport: Transport, rich: bool, long: bool
) -> None:
    bot, calls = transport
    send = bot.session.side_effect

    async def unchanged_first(owner: Bot, method: DeliveryMethod, **kwargs: object) -> Message:
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        return await send(owner, method, **kwargs)

    bot.session.side_effect = unchanged_first
    doc = Doc("a" * 4096 + "changed continuation" if long else "same")
    if rich:
        result = await reply_or_edit_rich(callback(source_message(bot)), doc, bot=bot)
    else:
        result = await reply_or_edit(callback(source_message(bot)), doc)
    assert result is None
    assert "".join(VisibleText(payload).text for payload in payloads(calls)) == ("changed continuation" if long else "")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("coordinator", ["streamer", "research", "streamer_without_placeholder"])
async def test_owned_final_lifecycle_reuses_and_removes_continuations(
    transport: Transport, test_redis: FakeAsyncRedis, coordinator: str
) -> None:
    bot, calls = transport
    if coordinator == "research":
        progress = await ResearchProgressMessage.send(source_message(bot))
    else:
        progress = ChatbotMessageStreamer(source_message(bot), "thinking", 0, redis=test_redis)
        if coordinator == "streamer":
            await progress.send_thinking_message()
    await progress.send_final(Doc("first " * 1800))
    sent_count = sum(isinstance(method, SendRichMessage) for method in calls)
    continuation_ids = [method.message_id for method in calls if isinstance(method, EditMessageText)]
    calls.clear()
    await progress.send_final(Doc("second " * 1500))
    assert not any(isinstance(method, SendRichMessage) for method in calls)
    assert sum(isinstance(method, EditMessageText) for method in calls) == sent_count
    assert len({method.message_id for method in calls}) == sent_count
    if continuation_ids:
        assert calls[0].message_id == continuation_ids[0]
    calls.clear()
    await progress.send_final(Doc("second " * 1500))
    assert not any(isinstance(method, (SendRichMessage, DeleteMessage)) for method in calls)
    calls.clear()
    await progress.send_final(Doc("short"))
    assert isinstance(calls[0], EditMessageText)
    assert len(calls) == sent_count
    assert all(isinstance(method, DeleteMessage) for method in calls[1:])
    calls.clear()
    await progress.send_final(Doc("short"))
    assert not any(isinstance(method, (SendRichMessage, DeleteMessage)) for method in calls)
    calls.clear()
    await progress.send_final(Doc("third " * 1800))
    assert isinstance(calls[0], EditMessageText)
    assert sum(isinstance(method, SendRichMessage) for method in calls) == sent_count - 1
    assert not any(isinstance(method, DeleteMessage) for method in calls)
    calls.clear()
    await progress.send_final(Doc("short again"))
    assert isinstance(calls[0], EditMessageText)
    assert sum(isinstance(method, DeleteMessage) for method in calls) == sent_count - 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("path", ["reply", "chat", "streamer", "research"])
async def test_ai_continuations_are_replyable_without_losing_body(
    transport: Transport, test_redis: FakeAsyncRedis, path: str
) -> None:
    bot, calls = transport
    body = "🌍 &<> answer " * 1200
    doc = build_ai_message_doc("footer", Bold(body))
    if path == "reply":
        await send_ai_rich_message(source_message(bot), doc)
    elif path == "chat":
        await send_ai_rich_message_to_chat(-100123, doc, bot=bot)
    elif path == "research":
        progress = await ResearchProgressMessage.send(source_message(bot))
        calls.clear()
        await progress.send_final(doc)
    else:
        streamer = ChatbotMessageStreamer(source_message(bot), "thinking", 0, redis=test_redis)
        await streamer.send_thinking_message()
        calls.clear()
        await streamer.send_final(doc)
    assert len(calls) > 1
    bodies: list[str] = []
    for payload in payloads(calls):
        marker = re.match(r'<tg-emoji emoji-id="(\d+)">([^<]+)</tg-emoji> ?', payload)
        visible = VisibleText(payload).text
        rich_text = (
            [RichTextCustomEmoji(custom_emoji_id=marker[1], alternative_text=marker[2]), visible[len(marker[2]) :]]
            if marker
            else visible
        )
        delivered = source_message(bot).model_copy(
            update={
                "from_user": User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie"),
                "text": None,
                "rich_message": RichMessage(blocks=[RichBlockParagraph(text=rich_text)]),
            }
        )
        reply = source_message(bot).model_copy(update={"reply_to_message": delivered})
        assert await AiReplyHandler.filter(reply)
        bodies.append(cut_titlebar(delivered))
    assert "".join(bodies) == body + "\nfooter"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("coordinator", ["streamer", "research"])
async def test_progress_edit_cleans_owned_final_continuations(
    transport: Transport, test_redis: FakeAsyncRedis, coordinator: str
) -> None:
    bot, calls = transport
    if coordinator == "research":
        progress = await ResearchProgressMessage.send(source_message(bot))
    else:
        progress = ChatbotMessageStreamer(source_message(bot), "thinking", 0, redis=test_redis)
        await progress.send_thinking_message()
    await progress.send_final(Doc("long " * 2000))
    continuation_ids = [101 + index for index in range(1, 3)]
    calls.clear()
    if coordinator == "research":
        await progress.update("searching")
    else:
        await progress.update_processing_activity("working")
    assert isinstance(calls[0], EditMessageText)
    assert {method.message_id for method in calls[1:] if isinstance(method, DeleteMessage)} == set(continuation_ids)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("coordinator", ["streamer", "research"])
@pytest.mark.parametrize("shrink", [False, True])
async def test_owned_final_unchanged_first_page_updates_existing_continuations(
    transport: Transport, test_redis: FakeAsyncRedis, coordinator: str, shrink: bool
) -> None:
    bot, calls = transport
    if coordinator == "research":
        progress = await ResearchProgressMessage.send(source_message(bot))
    else:
        progress = ChatbotMessageStreamer(source_message(bot), "thinking", 0, redis=test_redis)
        await progress.send_thinking_message()
    await progress.send_final(Doc("a" * 4096 + "old"))
    calls.clear()
    send = bot.session.side_effect

    async def unchanged_first(owner: Bot, method: DeliveryMethod, **kwargs: object) -> Message | bool:
        if isinstance(method, EditMessageText) and method.message_id == 101:
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        return await send(owner, method, **kwargs)

    bot.session.side_effect = unchanged_first
    result = await progress.send_final(Doc("a" * 4096 + ("" if shrink else "new")))
    assert isinstance(result, Message)
    assert result.message_id == 101
    assert len(calls) == 1
    assert isinstance(calls[0], DeleteMessage if shrink else EditMessageText)
    assert calls[0].message_id == 102
    if not shrink:
        assert payloads(calls) == ["new"]
    calls.clear()
    await progress.send_final(Doc("a" * 4096 + ("" if shrink else "new")))
    assert not any(isinstance(method, (SendRichMessage, DeleteMessage)) for method in calls)


@pytest.mark.asyncio(loop_scope="session")
async def test_lazy_translation_resolves_at_reply_boundary(transport: Transport) -> None:
    bot, calls = transport
    text = LazyProxy(lambda: "Translated <b>reply</b>")
    await reply_or_answer(source_message(bot), text)
    assert payloads(calls) == ["Translated <b>reply</b>"]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("parse_mode", [None, "HTML"])
async def test_paired_surrogate_string_and_lazy_replies_preserve_parse_mode(
    transport: Transport, lazy: bool, parse_mode: str | None
) -> None:
    bot, calls = transport
    body = "<b>\ud83c\udf0d &amp;</b>"
    text = LazyProxy(lambda: body) if lazy else body
    await reply_or_answer(source_message(bot), text, parse_mode=parse_mode)
    assert payloads(calls) == ["&lt;b&gt;🌍 &amp;amp;&lt;/b&gt;" if parse_mode is None else "<b>🌍 &amp;</b>"]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("repetitions", [1, 1000])
@pytest.mark.parametrize(
    "unsupported", [Image("photo"), Buttons(Button("action", url="https://example.org"))], ids=["image", "buttons"]
)
async def test_paired_surrogates_do_not_bypass_ordinary_html_tree_validation(
    transport: Transport, unsupported: Image | Buttons, repetitions: int
) -> None:
    bot, calls = transport
    doc = Doc(Bold("\ud83c\udf0d text " * repetitions), unsupported)
    with pytest.raises(ValueError):
        await reply_or_answer(source_message(bot), doc)
    assert calls == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("mode", ["html", "rich"])
@pytest.mark.parametrize("allow_short_rich", [False, True])
@pytest.mark.parametrize("repetitions", [1, 1000])
async def test_delivery_normalizes_paired_surrogates_without_losing_markup(
    transport: Transport, mode: Literal["html", "rich"], allow_short_rich: bool, repetitions: int
) -> None:
    bot, calls = transport
    body = "\ud83c\udf0d &<> 🌍\n" * repetitions
    doc = Doc(Bold(Url(body, "https://example.org/\ud83c\udf0d")))
    original = doc.to_rich() if mode == "rich" else doc.to_html()
    expected = original.replace("\ud83c\udf0d", "🌍")
    await deliver_doc(bot, -100123, doc, mode=mode, allow_short_rich=allow_short_rich, edit_message_id=20)
    assert isinstance(calls[0], EditMessageText)
    assert "".join(VisibleText(payload).text for payload in payloads(calls)) == VisibleText(expected).text
    assert all('<b><a href="https://example.org/🌍">' in payload for payload in payloads(calls))
    assert (len(calls) > 1) == (repetitions > 1)
    assert (doc.to_rich() if mode == "rich" else doc.to_html()) == original


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("mode", ["html", "rich"])
@pytest.mark.parametrize("allow_short_rich", [False, True])
@pytest.mark.parametrize(
    ("prefix", "codepoints"),
    [
        pytest.param("high ", (0xD800,), id="lone-high"),
        pytest.param("low ", (0xDC00,), id="lone-low"),
        pytest.param("reversed ", (0xDC00, 0xD800), id="reversed-pair"),
        pytest.param("mixed ", (0xD83C, 0xDF0D, 0xD800), id="paired-and-lone-high"),
    ],
)
async def test_delivery_rejects_unpaired_surrogates_before_transport(
    transport: Transport,
    mode: Literal["html", "rich"],
    allow_short_rich: bool,
    prefix: str,
    codepoints: tuple[int, ...],
) -> None:
    bot, calls = transport
    # Allure writes string parameters verbatim, so construct invalid Unicode only at the delivery boundary.
    invalid = prefix + "".join(chr(codepoint) for codepoint in codepoints)
    with pytest.raises(ValueError, match="unpaired surrogates"):
        await deliver_doc(bot, -100123, Doc(Bold(invalid)), mode=mode, allow_short_rich=allow_short_rich)
    assert calls == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delivery_normalizes_paired_surrogates_in_continuation_marker(transport: Transport) -> None:
    bot, calls = transport
    await deliver_doc(bot, -100123, Doc("a" * 4097), mode="rich", continuation_prefix="\ud83c\udf0d ")
    assert payloads(calls)[1].startswith("🌍 ")
    assert "".join(VisibleText(payload).text.removeprefix("🌍 ") for payload in payloads(calls)) == "a" * 4097


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("path", ["reply", "edit", "ai"])
async def test_delivery_requires_a_mounted_bot(path: str) -> None:
    source = source_message(Bot("123456:abcdefghijklmnopqrstuvwxyz0123456789")).as_(None)
    with pytest.raises(RuntimeError, match="mounted"):
        if path == "reply":
            await reply_or_answer(source, "reply")
        elif path == "edit":
            await reply_or_edit(source, "reply")
        else:
            await send_ai_rich_message(source, Doc("reply"))
