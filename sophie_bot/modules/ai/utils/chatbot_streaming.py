from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from random import choice
from typing import Any

from aiogram.types import InputRichMessage, Message
from redis.asyncio import Redis
from stfu_tg import Doc, HList, Italic, Template
from stfu_tg.ai_md import ai_markdown_to_doc
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils.ai_header import build_ai_progress_doc
from sophie_bot.modules.ai.utils.ai_progress import random_ai_thinking_text
from sophie_bot.modules.ai.utils.ai_send import editable_reply_markup, send_ai_rich_message
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME
from sophie_bot.modules.ai.utils.chatbot_response import build_reply_doc
from sophie_bot.modules.ai.utils.mention_usernames import MentionIndex, resolve_mention_index
from sophie_bot.modules.ai.utils.research import (
    ResearchProgressStage,
    random_research_progress_text,
)
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _

_DEFAULT_STREAM_BACKOFF_SECONDS = 1.5
_MIN_STREAM_BACKOFF_SECONDS = 0.5
# Telegram's limit also includes the animated marker, action status and three custom emoji.
_MAX_STREAM_TEXT_LENGTH = 4096 - 512
_MAX_REASONING_TAIL_LENGTH = 400


def _coerce_stream_backoff_seconds(value: object) -> float:
    if value is None:
        return _DEFAULT_STREAM_BACKOFF_SECONDS
    if isinstance(value, (int, float, str)):
        return max(float(value), _MIN_STREAM_BACKOFF_SECONDS)
    raise TypeError(f"Unsupported streaming backoff value: {value!r}")


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
        status: Element | str | None,
        throttle_seconds: float,
        *,
        redis: Redis,
        reasoning_as_tool: bool = False,
        stack_tools: bool = False,
        strip_alien_html_tags: bool = False,
    ) -> None:
        self.source_message = source_message
        self.redis = redis
        self.mention_index: MentionIndex | None = None
        self._mention_index_resolved = False
        self.placeholder = status or ""
        self.status: Element | None = None
        self.activity_history: list[Element | str] = []
        self.activity_started = False
        self.reasoning_as_tool = reasoning_as_tool
        self.stack_tools = stack_tools
        self.reasoning_seen = False
        self.reasoning: Element | None = None
        self.throttle_seconds = throttle_seconds
        self.strip_alien_html_tags = strip_alien_html_tags
        self.response_message: Message | None = None
        self.latest_text: str = ""
        self.last_sent_text: str = ""
        self.last_sent_at: float = 0.0
        self._last_sent_rich: str | None = None
        self._pending_update_task: asyncio.Task[None] | None = None

    async def send_thinking_message(self) -> None:
        doc = self._build_progress_doc(self.placeholder)
        self.response_message = await send_ai_rich_message(self.source_message, doc)
        self._last_sent_rich = doc.to_rich()

    async def stream(self, text: str) -> None:
        if not text.strip():
            return

        draft_text = _truncate_stream_text(text)
        had_status = self.status is not None
        had_activity = bool(self.activity_history)
        self.status = None
        self.activity_history.clear()
        self.latest_text = draft_text
        if draft_text == self.last_sent_text:
            if had_status or had_activity:
                await self._refresh_progress()
            else:
                await self._cancel_pending_update()
            return
        if self._throttled():
            await self._schedule_pending_update()
            return

        await self._cancel_pending_update()
        await self._flush_draft()

    async def stream_reasoning(self, reasoning_text: str) -> None:
        """Show reasoning once as an activity or keep its latest tail below the draft."""
        if self.response_message is None:
            return
        if self.reasoning_as_tool:
            if self.reasoning_seen or not reasoning_text.strip():
                return
            self.reasoning_seen = True
            self.activity_started = True
            label = _("Reasoning...")
            if self.stack_tools:
                self.activity_history.append(label)
                self.status = None
            else:
                self.status = Italic(label)
            await self._refresh_progress()
            return

        tail = _reasoning_tail(reasoning_text)
        if not tail:
            return

        self.reasoning = ai_markdown_to_doc(tail)
        self.activity_started = True
        self.status = None
        if not self._throttled():
            await self._refresh_progress()

    async def update_thinking_for_tool(self, tool_name: str) -> None:
        tool = AI_TOOLS_BY_NAME.get(tool_name)
        activity = (
            str(choice(tool.activity_texts)) if tool is not None and tool.activity_texts else _("Working on it...")
        )
        self.activity_started = True
        if self.stack_tools:
            self.activity_history.append(activity)
            self.status = None
            await self._refresh_progress()
        else:
            await self._update_status(Italic(activity))

    async def update_retrying(self, attempt: int, total_attempts: int) -> None:
        self.reasoning = None
        if self.stack_tools:
            self.activity_history.append(
                Template(_("Retrying ({attempt}/{total_attempts})..."), attempt=attempt, total_attempts=total_attempts)
            )
            self.activity_started = True
            self.status = None
            await self._refresh_progress()
            return
        await self._update_status(
            Italic(
                HList(
                    random_ai_thinking_text(),
                    Template(
                        _("(Retrying {attempt}/{total_attempts}...)"), attempt=attempt, total_attempts=total_attempts
                    ),
                    divider=" ",
                )
            )
        )

    async def update_research_progress(self, stage: ResearchProgressStage) -> None:
        await self._update_status(Italic(random_research_progress_text(stage)))

    async def send_final(self, doc: Doc, **reply_kwargs: Any) -> Message:
        await self._cancel_pending_update()
        rendered_rich = doc.to_rich()
        if self.response_message is None:
            return await send_ai_rich_message(self.source_message, doc, **reply_kwargs)

        reply_markup = editable_reply_markup(reply_kwargs.get("reply_markup"))
        if rendered_rich == self._last_sent_rich and reply_markup is None:
            return self.response_message

        result = await self.response_message.bot.edit_message_text(  # ty: ignore[unresolved-attribute]
            chat_id=self.response_message.chat.id,
            message_id=self.response_message.message_id,
            rich_message=InputRichMessage(html=rendered_rich),
            reply_markup=reply_markup,
        )
        self._last_sent_rich = rendered_rich
        return result if isinstance(result, Message) else self.response_message

    async def stop(self) -> None:
        """Cancel a deferred draft edit when the run ends without a normal final reply."""
        await self._cancel_pending_update()

    # ── Private helpers ────────────────────────────────────────────────────────

    def _throttled(self) -> bool:
        """Text and progress edits share one operator-tunable rate limit."""
        return time.monotonic() - self.last_sent_at < self.throttle_seconds

    async def _schedule_pending_update(self) -> None:
        if self._pending_update_task is not None:
            if not self._pending_update_task.done():
                return
            await self._cancel_pending_update()

        delay = max(0.0, self.throttle_seconds - (time.monotonic() - self.last_sent_at))
        self._pending_update_task = asyncio.create_task(self._send_pending_after(delay))

    async def _send_pending_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._flush_draft(force=True)

    async def _cancel_pending_update(self) -> None:
        task = self._pending_update_task
        self._pending_update_task = None
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _flush_draft(self, *, force: bool = False) -> None:
        if not self.latest_text or self.latest_text == self.last_sent_text:
            return
        if not force and self._throttled():
            await self._schedule_pending_update()
            return
        await self._update(await self._render_doc(self.latest_text))
        self.last_sent_text = self.latest_text

    async def _render_doc(self, text: str) -> Doc:
        if not self._mention_index_resolved and "@" in text:
            self.mention_index = await resolve_mention_index(
                self.source_message.chat.id,
                redis=self.redis,
            )
            self._mention_index_resolved = True
        body = await build_reply_doc(
            None,
            text,
            model=None,
            result=None,
            explicit_debug_mode=False,
            chat_tid=self.source_message.chat.id,
            redis=self.redis,
            mention_index=self.mention_index,
            strip_alien_html_tags=self.strip_alien_html_tags,
        )
        return self._build_progress_doc(body)

    def _build_progress_doc(self, body: Element | str) -> Doc:
        return build_ai_progress_doc(
            body,
            self.status,
            reasoning=self.reasoning,
            activity_history=self.activity_history,
        )

    async def _update_status(self, status: Element) -> None:
        self.activity_started = True
        self.status = status
        await self._refresh_progress()

    async def _refresh_progress(self) -> None:
        await self._cancel_pending_update()

        # The agent can call a tool after narrating, so keep the draft under the new trace.
        draft_text = self.latest_text or self.last_sent_text
        doc = (
            await self._render_doc(draft_text)
            if draft_text
            else self._build_progress_doc("" if self.activity_started else self.placeholder)
        )
        await self._update(doc)
        self.last_sent_text = draft_text

    async def _update(self, doc: Doc) -> None:
        """Edit the placeholder in place; Telegram edit failures propagate to the caller."""
        rendered_rich = doc.to_rich()
        if rendered_rich == self._last_sent_rich:
            return

        if self.response_message is None:
            self.response_message = await send_ai_rich_message(self.source_message, doc)
        else:
            await self.response_message.bot.edit_message_text(  # ty: ignore[unresolved-attribute]
                chat_id=self.response_message.chat.id,
                message_id=self.response_message.message_id,
                rich_message=InputRichMessage(html=rendered_rich),
            )

        self._last_sent_rich = rendered_rich
        self.last_sent_at = time.monotonic()


async def build_message_streamer(
    message: Message,
    explicit_debug_mode: bool,
    *,
    redis: Redis,
    strip_alien_html_tags: bool = False,
) -> ChatbotMessageStreamer | None:
    if explicit_debug_mode:
        return None

    backoff, reasoning_as_tool, stack_tools = await asyncio.gather(
        get_value("ai_chatbot_streaming_backoff_seconds", chat_tid=message.chat.id, redis=redis),
        is_enabled("ai_chatbot_reasoning_as_tool", chat_tid=message.chat.id, redis=redis),
        is_enabled("ai_chatbot_stack_progress_tools", chat_tid=message.chat.id, redis=redis),
    )
    streamer = ChatbotMessageStreamer(
        source_message=message,
        status=random_ai_thinking_text(),
        throttle_seconds=_coerce_stream_backoff_seconds(backoff),
        reasoning_as_tool=reasoning_as_tool,
        stack_tools=stack_tools,
        redis=redis,
        strip_alien_html_tags=strip_alien_html_tags,
    )
    await streamer.send_thinking_message()
    return streamer
