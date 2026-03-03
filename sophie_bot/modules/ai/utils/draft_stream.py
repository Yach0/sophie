from __future__ import annotations

import time

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import Message

from sophie_bot.services.bot import bot

DEFAULT_DRAFT_THROTTLE_SECONDS = 0.35
DEFAULT_DRAFT_MAX_TEXT_LENGTH = 4096


class MessageDraftStreamer:
    def __init__(
        self,
        message: Message,
        enabled: bool,
        throttle_seconds: float = DEFAULT_DRAFT_THROTTLE_SECONDS,
        max_text_length: int = DEFAULT_DRAFT_MAX_TEXT_LENGTH,
    ) -> None:
        self.message = message
        self.enabled = enabled
        self.throttle_seconds = throttle_seconds
        self.max_text_length = max_text_length
        self.draft_id = message.message_id if message.message_id > 0 else 1
        self.last_sent_text = ""
        self.last_sent_at = 0.0

    async def stream(self, text: str) -> None:
        if not self.enabled:
            return

        if not text.strip():
            return

        now = time.monotonic()
        if now - self.last_sent_at < self.throttle_seconds:
            return

        draft_text = text[: self.max_text_length]
        if len(text) > self.max_text_length:
            draft_text = f"{draft_text[:-3]}..."

        if draft_text == self.last_sent_text:
            return

        try:
            await bot.send_message_draft(
                chat_id=self.message.chat.id,
                draft_id=self.draft_id,
                text=draft_text,
                parse_mode=None,
                entities=None,
            )
        except (TelegramBadRequest, TelegramAPIError):
            # Unsupported BotAPI versions or unsupported contexts can reject draft streaming.
            self.enabled = False
            return

        self.last_sent_text = draft_text
        self.last_sent_at = now
