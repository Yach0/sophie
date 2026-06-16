from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from random import choice
from typing import Any

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputRichMessage, Message, ReplyParameters  # ty: ignore[unresolved-import]
from pydantic_ai.models import Model
from stfu_tg import Doc, HList, Template
from stfu_tg.doc import Element

from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_progress import (
    ai_progress_custom_emoji,
    random_ai_progress_custom_emoji_id,
    random_ai_thinking_text,
)
from sophie_bot.modules.ai.utils.chatbot_response import build_chatbot_header, build_reply_doc
from sophie_bot.modules.ai.utils.draft_stream import DEFAULT_DRAFT_MAX_TEXT_LENGTH
from sophie_bot.modules.ai.utils.research import (
    ResearchProgressStage,
    random_research_progress_text,
    research_progress_suffix,
)
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _

ToolCallCallback = Callable[[str], Awaitable[None]]


_DEFAULT_STREAM_BACKOFF_SECONDS = 1.5
_MIN_STREAM_BACKOFF_SECONDS = 0.5
_MAX_STREAM_TEXT_LENGTH = DEFAULT_DRAFT_MAX_TEXT_LENGTH - 128
_TOOL_THINKING_TEXTS: dict[str, tuple[str, ...]] = {
    "tavily_search": (
        _("Searching the web..."),
        _("Looking it up online..."),
        _("Browsing the internet..."),
    ),
    "kagi_search": (
        _("Searching the web..."),
        _("Looking it up online..."),
        _("Browsing the internet..."),
    ),
    "write_memory": (
        _("Updating memory..."),
        _("Saving to memory..."),
    ),
    "forget_memory": (
        _("Removing from memory..."),
        _("Forgetting..."),
    ),
    "cmds_help": (
        _("Checking command help..."),
        _("Looking up commands..."),
    ),
    "get_notes": (
        _("Scanning notes..."),
        _("Looking through notes..."),
    ),
    "get_note_content": (
        _("Reading note..."),
        _("Fetching note content..."),
    ),
    "save_note": (
        _("Saving note..."),
        _("Writing to notes..."),
    ),
    "delete_note": (
        _("Deleting note..."),
        _("Removing note..."),
    ),
    "research_topic": (_("Starting the research..."),),
}


def _thinking_header_element(emoji_id: str | None = None) -> Element:
    return HList(ai_progress_custom_emoji(emoji_id), random_ai_thinking_text(), divider=" ")


def _coerce_stream_backoff_seconds(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(float(value), _MIN_STREAM_BACKOFF_SECONDS)

    try:
        return max(float(str(value)), _MIN_STREAM_BACKOFF_SECONDS)
    except ValueError:
        return _DEFAULT_STREAM_BACKOFF_SECONDS


def _truncate_stream_text(output_text: str) -> str:
    if len(output_text) <= _MAX_STREAM_TEXT_LENGTH:
        return output_text
    return f"{output_text[: _MAX_STREAM_TEXT_LENGTH - 3]}..."


@dataclass(slots=True)
class ChatbotMessageStreamer:
    source_message: Message
    header: Element
    enabled: bool
    throttle_seconds: float
    response_message: Message | None = None
    tool_thinking_texts: dict[str, tuple[str, ...]] | None = None
    connection: ChatConnection | None = None
    model: Model | None = None
    emoji_id: str | None = None
    last_sent_text: str = ""
    last_sent_at: float = 0.0
    use_rich_streaming: bool = False

    async def send_thinking_message(self) -> None:
        self.response_message = await self.source_message.reply(
            Doc(self.header).to_html(),
            disable_web_page_preview=True,
        )

    async def stream(self, text: str) -> None:
        if not self.enabled or not text.strip():
            return

        monotonic_time = time.monotonic()
        if monotonic_time - self.last_sent_at < self.throttle_seconds:
            return

        draft_text = _truncate_stream_text(text)
        if draft_text == self.last_sent_text:
            return

        doc = await build_reply_doc(
            self.header,
            draft_text,
            model=None,
            result=None,
            explicit_debug_mode=False,
            chat_tid=self.source_message.chat.id,
        )
        if not await self._edit_or_send(doc):
            self.enabled = False
            return

        self.last_sent_text = draft_text
        self.last_sent_at = monotonic_time

    async def update_thinking_for_tool(self, tool_name: str) -> None:
        if not self.tool_thinking_texts:
            return

        texts = self.tool_thinking_texts.get(tool_name)
        if not texts:
            return

        await self._update_thinking_header(HList(ai_progress_custom_emoji(self.emoji_id), choice(texts), divider=" "))

    async def update_retrying(self, attempt: int, total_attempts: int) -> None:
        await self._update_thinking_header(
            HList(
                ai_progress_custom_emoji(self.emoji_id),
                random_ai_thinking_text(),
                Template(_("(Retrying {attempt}/{total_attempts}...)"), attempt=attempt, total_attempts=total_attempts),
                divider=" ",
            )
        )

    async def update_research_progress(self, stage: ResearchProgressStage) -> None:
        text = random_research_progress_text(stage)
        suffix = research_progress_suffix(stage)
        await self._update_thinking_header(HList(ai_progress_custom_emoji(self.emoji_id), text, suffix, divider=" "))

    async def _update_thinking_header(self, thinking_element: Element) -> None:
        if not self.connection or not self.model:
            return

        self.header = await build_chatbot_header(
            self.connection.db_model.iid,
            self.model,
            [],
            additional_header_items=[thinking_element],
            skip_battery=True,
        )
        if self.response_message is None:
            return

        try:
            await self.response_message.edit_text(
                text=Doc(self.header).to_html(),
                disable_web_page_preview=True,
            )
        except TelegramAPIError:
            pass

    async def send_final(self, doc: Doc, **reply_kwargs: Any) -> Message:
        if self.use_rich_streaming:
            return await self._send_rich_final(doc, **reply_kwargs)

        if self.response_message is None:
            return await self.source_message.reply(
                doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )

        try:
            edited_message = await self.response_message.edit_text(
                text=doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )
            if isinstance(edited_message, Message):
                return edited_message
            return self.response_message
        except TelegramAPIError:
            return await self.source_message.reply(
                doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )

    async def _send_rich_final(self, doc: Doc, **reply_kwargs: Any) -> Message:
        if self.response_message is not None:
            try:
                await self.response_message.delete()
            except TelegramAPIError:
                pass

        try:
            return await self.source_message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
                chat_id=self.source_message.chat.id,
                rich_message=InputRichMessage(html=doc.to_rich()),  # ty: ignore[unresolved-attribute]
                reply_parameters=ReplyParameters(message_id=self.source_message.message_id),
                message_thread_id=self.source_message.message_thread_id,
                reply_markup=reply_kwargs.get("reply_markup"),
            )
        except TelegramAPIError:
            return await self.source_message.reply(
                doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )

    async def _edit_or_send(self, doc: Doc) -> bool:
        try:
            if self.response_message is None:
                self.response_message = await self.source_message.reply(
                    doc.to_html(),
                    disable_web_page_preview=True,
                )
                return True

            await self.response_message.edit_text(text=doc.to_html(), disable_web_page_preview=True)
            return True
        except TelegramAPIError:
            return False


async def build_message_streamer(
    message: Message,
    connection: ChatConnection,
    model: Model,
    explicit_debug_mode: bool,
) -> ChatbotMessageStreamer | None:
    if explicit_debug_mode or message.chat.type == ChatType.PRIVATE:
        return None

    thinking_enabled = await is_enabled("ai_chatbot_thinking_message", chat_tid=message.chat.id)
    streaming_enabled = await is_enabled("ai_chatbot_streaming", chat_tid=message.chat.id)
    rich_streaming_enabled = await is_enabled("ai_chatbot_rich_streaming", chat_tid=message.chat.id)
    if not thinking_enabled and not streaming_enabled and not rich_streaming_enabled:
        return None

    header_items = None
    emoji_id = None
    if thinking_enabled:
        if await is_enabled("ai_chatbot_random_emoji", chat_tid=message.chat.id):
            emoji_id = random_ai_progress_custom_emoji_id()
        header_items = [_thinking_header_element(emoji_id=emoji_id)]
    header = await build_chatbot_header(
        connection.db_model.iid,
        model,
        [],
        additional_header_items=header_items,
        skip_battery=thinking_enabled,
    )
    backoff_seconds = _coerce_stream_backoff_seconds(
        await get_value("ai_chatbot_streaming_backoff_seconds", chat_tid=message.chat.id)
    )
    streamer = ChatbotMessageStreamer(
        source_message=message,
        header=header,
        enabled=streaming_enabled,
        throttle_seconds=backoff_seconds,
        tool_thinking_texts=_TOOL_THINKING_TEXTS
        if thinking_enabled and await is_enabled("ai_chatbot_tool_thinking", chat_tid=message.chat.id)
        else None,
        connection=connection,
        model=model,
        emoji_id=emoji_id,
        use_rich_streaming=rich_streaming_enabled,
    )
    if thinking_enabled:
        await streamer.send_thinking_message()
    return streamer
