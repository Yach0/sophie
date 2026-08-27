from __future__ import annotations

import time
from enum import Enum
from random import choice
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputRichMessage, Message
from pydantic_ai.models import Model
from stfu_tg import Doc, Template
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils.ai_progress import (
    ai_progress_line,
    random_ai_progress_custom_emoji_id,
    random_ai_thinking_text,
)
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message
from sophie_bot.modules.ai.utils.chatbot_response import build_reply_doc
from sophie_bot.modules.ai.utils.mention_usernames import MentionIndex, resolve_mention_index
from sophie_bot.modules.ai.utils.research import (
    ResearchProgressStage,
    random_research_progress_text,
    research_progress_suffix,
)
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _

_DEFAULT_STREAM_BACKOFF_SECONDS = 1.5
_MIN_STREAM_BACKOFF_SECONDS = 0.5
# Telegram's message limit, less room for the header and the credit indicator.
_MAX_STREAM_TEXT_LENGTH = 4096 - 128
# Reasoning is shown inline in the one-line header, so only its tail fits.
_MAX_REASONING_TAIL_LENGTH = 200
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
    "tinyfish_search": (
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
    "sophie_help": (
        _("Checking the documentation..."),
        _("Looking up how Sophie works..."),
    ),
    "sophie_inspect": (
        _("Digging through my own sources..."),
        _("Reading my own code..."),
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


class StreamMode(Enum):
    THINKING_ONLY = "thinking_only"  # title bar only, no streaming updates
    HTML_EDIT = "html_edit"  # group + streaming: HTML reply → edit in place
    RICH_EDIT = "rich_edit"  # group + rich_streaming: rich send → editMessageText(rich_message=)


def _thinking_header_element(emoji_id: str | None = None) -> Element:
    return ai_progress_line(random_ai_thinking_text(), emoji_id)


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


def _reasoning_tail(reasoning_text: str) -> str:
    # Collapse only the slice that can survive truncation: reasoning traces run to thousands of
    # characters and this is called for every update.
    tail = reasoning_text[-(_MAX_REASONING_TAIL_LENGTH * 4) :]
    collapsed = " ".join(tail.split())
    if len(collapsed) <= _MAX_REASONING_TAIL_LENGTH and len(tail) == len(reasoning_text):
        return collapsed
    return f"...{collapsed[-_MAX_REASONING_TAIL_LENGTH:]}"


class ChatbotMessageStreamer:
    def __init__(
        self,
        source_message: Message,
        header: Element,
        mode: StreamMode,
        throttle_seconds: float,
        tool_thinking_texts: dict[str, tuple[str, ...]] | None = None,
        emoji_id: str | None = None,
    ) -> None:
        self.source_message = source_message
        self.mention_index: MentionIndex | None = None
        self._mention_index_resolved = False
        self.header = header
        self.mode = mode
        self.throttle_seconds = throttle_seconds
        self.tool_thinking_texts = tool_thinking_texts
        self.emoji_id = emoji_id
        self.response_message: Message | None = None
        self.last_sent_text: str = ""
        self.last_sent_at: float = 0.0

    async def send_thinking_message(self) -> None:
        match self.mode:
            case StreamMode.RICH_EDIT:
                self.response_message = await self._send_rich_reply(Doc(self.header))
            case _:
                self.response_message = await self.source_message.reply(
                    Doc(self.header).to_html(),
                    disable_web_page_preview=True,
                )

    async def stream(self, text: str) -> None:
        if self.mode == StreamMode.THINKING_ONLY or not text.strip() or self._throttled():
            return

        draft_text = _truncate_stream_text(text)
        if draft_text == self.last_sent_text:
            return

        if await self._update(await self._render_doc(draft_text)):
            self.last_sent_text = draft_text

    async def stream_reasoning(self, reasoning_text: str) -> None:
        """Show the tail of the model's own reasoning in the thinking header while it works.

        Placeholder content only: reasoning never becomes part of the final message and never
        reaches the message cache.
        """
        if self.response_message is None or self._throttled():
            return

        tail = _reasoning_tail(reasoning_text)
        if not tail:
            return

        await self._update_thinking_header(ai_progress_line(tail, self.emoji_id))

    async def update_thinking_for_tool(self, tool_name: str) -> None:
        if not self.tool_thinking_texts:
            return
        texts = self.tool_thinking_texts.get(tool_name)
        if not texts:
            return
        await self._update_thinking_header(ai_progress_line(choice(texts), self.emoji_id))

    async def update_retrying(self, attempt: int, total_attempts: int) -> None:
        await self._update_thinking_header(
            ai_progress_line(
                random_ai_thinking_text(),
                self.emoji_id,
                Template(_("(Retrying {attempt}/{total_attempts}...)"), attempt=attempt, total_attempts=total_attempts),
            )
        )

    async def update_research_progress(self, stage: ResearchProgressStage) -> None:
        text = random_research_progress_text(stage)
        suffix = research_progress_suffix(stage)
        await self._update_thinking_header(ai_progress_line(text, self.emoji_id, suffix))

    async def send_final(self, doc: Doc, **reply_kwargs: Any) -> Message:
        if self.response_message is None:
            return await self.source_message.reply(
                doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )

        # For all edit-based modes: update in place if content changed, reply fresh on error.
        if doc.to_html() == self.last_sent_text and self.mode != StreamMode.RICH_EDIT:
            return self.response_message

        try:
            reply_markup = reply_kwargs.get("reply_markup")
            if not isinstance(reply_markup, InlineKeyboardMarkup):
                reply_markup = None
            if self.mode == StreamMode.RICH_EDIT:
                result = await self.response_message.bot.edit_message_text(  # ty: ignore[unresolved-attribute]
                    chat_id=self.response_message.chat.id,
                    message_id=self.response_message.message_id,
                    rich_message=InputRichMessage(html=doc.to_rich()),
                    reply_markup=reply_markup,
                )
            else:
                result = await self.response_message.edit_text(
                    text=doc.to_html(),
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                )
            return result if isinstance(result, Message) else self.response_message
        except TelegramAPIError:
            if self.mode == StreamMode.RICH_EDIT:
                return await send_ai_rich_message(self.source_message, doc, **reply_kwargs)
            try:
                return await self.source_message.reply(
                    doc.to_html(),
                    disable_web_page_preview=True,
                    **reply_kwargs,
                )
            except TelegramAPIError:
                return await self.source_message.bot.send_message(  # ty: ignore[unresolved-attribute]
                    chat_id=self.source_message.chat.id,
                    text=doc.to_html(),
                    disable_web_page_preview=True,
                    message_thread_id=self.source_message.message_thread_id,
                    **reply_kwargs,
                )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _throttled(self) -> bool:
        """Text and header updates edit the same message, so they share one operator-tunable rate
        limit instead of each getting its own."""
        return time.monotonic() - self.last_sent_at < self.throttle_seconds

    async def _render_doc(self, text: str) -> Doc:
        if not self._mention_index_resolved and "@" in text:
            self.mention_index = await resolve_mention_index(self.source_message.chat.id)
            self._mention_index_resolved = True
        return await build_reply_doc(
            self.header,
            text,
            model=None,
            result=None,
            explicit_debug_mode=False,
            chat_tid=self.source_message.chat.id,
            mention_index=self.mention_index,
        )

    async def _update_thinking_header(self, thinking_element: Element) -> None:
        self.header = thinking_element

        # The agent loop can keep going after it has already written text (narrate, call a tool,
        # answer), so a header-only doc here would wipe what the user is reading. Re-render the
        # streamed text under the new header instead.
        doc = await self._render_doc(self.last_sent_text) if self.last_sent_text else Doc(self.header)
        await self._update(doc)

    async def _update(self, doc: Doc) -> bool:
        """Edit the placeholder in place. Returns False if the edit failed and updates should stop."""
        try:
            if self.response_message is None:
                self.response_message = await self.source_message.reply(
                    doc.to_html(),
                    disable_web_page_preview=True,
                )
            elif self.mode == StreamMode.RICH_EDIT:
                await self.response_message.bot.edit_message_text(  # ty: ignore[unresolved-attribute]
                    chat_id=self.response_message.chat.id,
                    message_id=self.response_message.message_id,
                    rich_message=InputRichMessage(html=doc.to_rich()),
                )
            else:
                await self.response_message.edit_text(
                    text=doc.to_html(),
                    disable_web_page_preview=True,
                )
        except TelegramAPIError:
            return False

        self.last_sent_at = time.monotonic()
        return True

    async def _send_rich_reply(self, doc: Doc, **reply_kwargs: Any) -> Message:
        return await send_ai_rich_message(self.source_message, doc, **reply_kwargs)


async def build_message_streamer(
    message: Message,
    model: Model,
    explicit_debug_mode: bool,
) -> ChatbotMessageStreamer | None:
    if explicit_debug_mode:
        return None

    thinking_enabled = await is_enabled("ai_chatbot_thinking_message", chat_tid=message.chat.id)
    streaming_enabled = await is_enabled("ai_chatbot_streaming", chat_tid=message.chat.id)
    rich_streaming_enabled = await is_enabled("ai_chatbot_rich_streaming", chat_tid=message.chat.id)

    if rich_streaming_enabled:
        mode = StreamMode.RICH_EDIT
    elif streaming_enabled:
        mode = StreamMode.HTML_EDIT
    elif thinking_enabled:
        mode = StreamMode.THINKING_ONLY
    else:
        return None

    # Picked once per run, so every edit of the placeholder keeps the same emoji. Without the flag
    # `ai_progress_custom_emoji` uses the fixed default one — the choice never depends on whether
    # the thinking text is shown.
    emoji_id = (
        random_ai_progress_custom_emoji_id()
        if await is_enabled("ai_chatbot_random_emoji", chat_tid=message.chat.id)
        else None
    )

    # Placeholder only — the AI table header and its battery are built once the answer is ready.
    header = (
        _thinking_header_element(emoji_id=emoji_id)
        if thinking_enabled
        else ai_progress_line(model.model_name, emoji_id)
    )
    backoff_seconds = _coerce_stream_backoff_seconds(
        await get_value("ai_chatbot_streaming_backoff_seconds", chat_tid=message.chat.id)
    )
    streamer = ChatbotMessageStreamer(
        source_message=message,
        header=header,
        mode=mode,
        throttle_seconds=backoff_seconds,
        tool_thinking_texts=_TOOL_THINKING_TEXTS
        if thinking_enabled and await is_enabled("ai_chatbot_tool_thinking", chat_tid=message.chat.id)
        else None,
        emoji_id=emoji_id,
    )
    await streamer.send_thinking_message()
    return streamer
