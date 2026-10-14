"""Ordered text delivery; STFU owns layout and UTF-16 partitioning."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Literal

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message
from stfu_tg import Doc, PreformattedHTML, Template, partition_doc
from stfu_tg.doc import Element

from sophie_bot.constants import TELEGRAM_MESSAGE_LENGTH_LIMIT
from sophie_bot.modules.utils_.telegram_exceptions import MSG_NOT_MODIFIED, REPLIED_NOT_FOUND, REPLY_MESSAGE_INVALID

_SEND_ONLY_OPTIONS = {
    "message_thread_id",
    "reply_parameters",
    "disable_notification",
    "protect_content",
    "allow_paid_broadcast",
    "message_effect_id",
    "ephemeral_message_parameters",
    "direct_messages_topic_id",
    "suggested_post_parameters",
}


class _VisibleSize(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.units = 0
        self.feed(source)
        self.close()

    def handle_data(self, data: str) -> None:
        self.units += len(data.encode("utf-16-le")) // 2

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.units += 1


def partition_delivery_doc(
    doc: Doc,
    *,
    mode: Literal["html", "rich"],
    allow_short_rich: bool = False,
    continuation_prefix: str = "",
) -> list[Doc]:
    """Partition the selected layout; short existing Rich payloads need no partitioning."""
    if mode == "rich":
        rendered = doc.to_rich()
        if allow_short_rich and rendered and _VisibleSize(rendered).units <= TELEGRAM_MESSAGE_LENGTH_LIMIT:
            return [Doc(Template(rendered))]
        # Preserve structural newlines in the already-rendered Rich layout.
        doc = Doc(Template(rendered))
    chunks = partition_doc(doc, TELEGRAM_MESSAGE_LENGTH_LIMIT, mode=mode)
    if continuation_prefix and len(chunks) > 1:
        # Reserve the marker before partitioning so no content is truncated.
        chunks = partition_doc(doc, TELEGRAM_MESSAGE_LENGTH_LIMIT - _VisibleSize(continuation_prefix).units, mode=mode)
        chunks = [chunks[0], *(Doc(Template(continuation_prefix + chunk.to_rich())) for chunk in chunks[1:])]
    return chunks


async def deliver_doc(
    bot: Bot,
    chat_id: int,
    text: Element | str,
    *,
    mode: Literal["html", "rich"] = "html",
    edit_message_id: int | None = None,
    allow_deleted_reply: bool = False,
    allow_short_rich: bool = False,
    continuation_prefix: str = "",
    continuation_message_ids: list[int] | None = None,
    **kwargs: Any,
) -> Message | bool | None:
    """Return the first send/edit result after every continuation succeeds.

    Partition attempts validate text-only documents before delivery. The existing
    short Rich path can opt out of partitioning. Captions, explicit entities and
    Markdown parse modes belong to other boundaries.
    A keyboard is attached only to the first payload. Sends retain the original
    reply and topic, including continuations after an edit. Unchanged edits return
    None and do not stop delivery. Coordinators may supply a lifetime-local list
    of continuation IDs, updated after each successful send/delete, for reuse and
    surplus cleanup. Other failures propagate.
    """
    if kwargs.get("entities") is not None or "caption" in kwargs:
        raise ValueError("Document delivery does not support entities or captions")
    kwargs.pop("entities", None)
    parse_mode = kwargs.pop("parse_mode", "HTML")
    if parse_mode is not None and str(parse_mode).lower() != "html":
        raise ValueError("Document delivery supports HTML or plain text, not Markdown")
    if isinstance(text, Element):
        doc = text if isinstance(text, Doc) else Doc(text)
    else:
        doc = Doc(text) if parse_mode is None else Doc(PreformattedHTML(text))
    chunks = partition_delivery_doc(
        doc, mode=mode, allow_short_rich=allow_short_rich, continuation_prefix=continuation_prefix
    )
    if not chunks:
        raise ValueError("Cannot deliver an empty document")
    first_result: Message | bool | None = None
    for index, chunk in enumerate(chunks):
        options = dict(kwargs)
        if index:
            options.pop("reply_markup", None)
        payload = (
            {"rich_message": InputRichMessage(html=chunk.to_rich())}
            if mode == "rich"
            else {"text": chunk.to_html(), "parse_mode": "HTML"}
        )
        target_message_id = (
            edit_message_id
            if index == 0
            else (
                continuation_message_ids[index - 1]
                if continuation_message_ids is not None and index <= len(continuation_message_ids)
                else None
            )
        )
        if target_message_id is not None:
            options = {key: value for key, value in options.items() if key not in _SEND_ONLY_OPTIONS}
            try:
                result = await bot.edit_message_text(
                    chat_id=chat_id, message_id=target_message_id, **payload, **options
                )
            except TelegramBadRequest as error:
                if MSG_NOT_MODIFIED not in error.message:
                    raise
                result = None
        else:
            if mode == "rich":
                options.pop("disable_web_page_preview", None)
            send = bot.send_rich_message if mode == "rich" else bot.send_message
            try:
                result = await send(chat_id=chat_id, **payload, **options)
            except TelegramBadRequest as error:
                if not allow_deleted_reply or not any(
                    reason in error.message for reason in (REPLIED_NOT_FOUND, REPLY_MESSAGE_INVALID)
                ):
                    raise
                options.pop("reply_parameters", None)
                kwargs.pop("reply_parameters", None)
                result = await send(chat_id=chat_id, **payload, **options)
            if index and continuation_message_ids is not None:
                continuation_message_ids.append(result.message_id)
        if index == 0:
            first_result = result
    if continuation_message_ids is not None:
        while len(continuation_message_ids) >= len(chunks):
            await bot.delete_message(chat_id=chat_id, message_id=continuation_message_ids[-1])
            continuation_message_ids.pop()
    return first_result
