from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendRichMessage
from aiogram.types import InputRichMessage, Message
from redis.asyncio import Redis
from stfu_tg import BlockQuote, Doc, Paragraph, PreformattedHTML
from stfu_tg.ai_md import ai_markdown_to_doc

from sophie_bot.modules.ai.utils.ai_header import (
    AI_CUSTOM_EMOJI_ID,
    AI_GENERATING_EMOJI_ID,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message
from sophie_bot.modules.ai.utils.chatbot_response import build_reply_doc
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer

MARKDOWN = "Hello **bold and *italic* text**.\n\nSecond [link](https://example.com) & `code`."
FIRST = "Hello <b>bold and <i>italic</i> text</b>."
SECOND = '<p>Second <a href="https://example.com">link</a> &amp; <code>code</code>.</p>'


def test_real_stfu_renderer_preserves_paragraphs_and_plain_html_separator() -> None:
    body = ai_markdown_to_doc(MARKDOWN)
    assert body.to_rich() == f"<p>{FIRST}</p>\n{SECOND}"
    assert body.to_html() == f"{FIRST}\n{SECOND[3:-4]}"


@pytest.mark.parametrize("preformatted", [False, True])
@pytest.mark.parametrize("with_header", [False, True])
def test_only_leading_paragraph_is_inline_when_header_present(with_header: bool, preformatted: bool) -> None:
    body = ai_markdown_to_doc(MARKDOWN)
    rendered_body = PreformattedHTML(body.to_rich()) if preformatted else body
    header = build_ai_header("simple", ai_credit_header(50)) if with_header else None
    rich = build_ai_message_doc(header, rendered_body).to_rich()
    if with_header:
        assert rich.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> {FIRST}\n{SECOND}')
        assert rich.endswith(" 50%")
    else:
        assert rich == body.to_rich()


def test_only_first_body_item_is_unwrapped() -> None:
    rich = build_ai_message_doc("battery", None, Paragraph("First"), Paragraph("Second")).to_rich()
    assert rich.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> First <p>Second</p>')


@pytest.mark.parametrize(
    "markdown",
    [
        "# Heading\n\nLater paragraph.",
        "- **item**\n- next\n\nLater paragraph.",
        "1. **item**\n2. next\n\nLater paragraph.",
        "| Name | Value |\n| --- | --- |\n| **key** | value |\n\nLater paragraph.",
        "> quoted *text*\n\nLater paragraph.",
        "```html\n<p>literal</p>\n```\n\nLater paragraph.",
        ":::details More\nNested paragraph.\n\nSecond nested.\n:::\n\nLater paragraph.",
    ],
)
def test_leading_blocks_and_their_later_paragraphs_are_preserved(markdown: str) -> None:
    body = ai_markdown_to_doc(markdown)
    rich = build_ai_message_doc("battery", body).to_rich()
    assert rich == f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> {body.to_rich()}\nbattery'


def test_nested_paragraph_in_quote_is_preserved() -> None:
    body = Doc(Paragraph("First"), BlockQuote(Paragraph("Quoted")), Paragraph("Last"))
    rich = build_ai_message_doc("battery", body).to_rich()
    assert "First\n<blockquote><p>Quoted</p></blockquote>\n<p>Last</p>" in rich


def test_plain_html_fallback_keeps_newlines_and_inline_formatting() -> None:
    doc = build_ai_message_doc("battery", ai_markdown_to_doc(MARKDOWN))
    assert doc.to_html() == f"✨ {FIRST}\n{SECOND[3:-4]}\nbattery"
    assert doc.to_md() == f"✨ {ai_markdown_to_doc(MARKDOWN).to_md()}\nbattery"


@pytest.mark.usefixtures("db_init")
@pytest.mark.parametrize("strip_alien_html_tags", [False, True])
async def test_reply_pipeline_preserves_second_paragraph(test_redis: Redis, strip_alien_html_tags: bool) -> None:
    doc = await build_reply_doc(
        "battery",
        "Hello **bold**.\n\nSecond <i>italic</i>.",
        None,
        None,
        False,
        -100123,
        redis=test_redis,
        strip_alien_html_tags=strip_alien_html_tags,
    )
    assert "Hello <b>bold</b>.\n<p>Second " in doc.to_rich()
    assert "</p>\nbattery" in doc.to_rich()
    if strip_alien_html_tags:
        assert "<p>Second <i>italic</i>.</p>" in doc.to_rich()


@pytest.mark.usefixtures("db_init")
async def test_streamed_draft_and_final_edit_preserve_paragraphs(test_redis: Redis) -> None:
    edit = AsyncMock()
    bot = SimpleNamespace(edit_message_text=edit)
    source = SimpleNamespace(chat=SimpleNamespace(id=-100123), message_id=7, bot=bot)
    streamer = ChatbotMessageStreamer(cast(Message, source), "thinking", 0, redis=test_redis)
    streamer.response_message = cast(Message, SimpleNamespace(chat=source.chat, message_id=8, bot=bot))
    await streamer.stream(MARKDOWN)
    draft = edit.await_args.kwargs["rich_message"].html
    assert draft.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji> {FIRST}\n{SECOND}')
    final = await build_reply_doc("battery", MARKDOWN, None, None, False, -100123, redis=test_redis)
    await streamer.send_final(final)
    assert edit.await_args.kwargs["rich_message"].html == final.to_rich()
    assert SECOND in final.to_rich()
    await streamer.stop()


async def test_deleted_source_retry_keeps_same_rich_paragraphs() -> None:
    error = TelegramBadRequest(
        method=SendRichMessage(chat_id=1, rich_message=InputRichMessage(html="answer")),
        message="message to be replied not found",
    )
    send = AsyncMock(side_effect=[error, SimpleNamespace(message_id=9)])
    source = SimpleNamespace(
        chat=SimpleNamespace(id=1), message_id=7, message_thread_id=None, bot=SimpleNamespace(send_rich_message=send)
    )
    doc = build_ai_message_doc("battery", ai_markdown_to_doc(MARKDOWN))
    await send_ai_rich_message(cast(Message, source), doc)
    assert send.await_count == 2
    for call in send.await_args_list:
        assert call.kwargs["rich_message"].html == doc.to_rich()
        assert SECOND in call.kwargs["rich_message"].html
