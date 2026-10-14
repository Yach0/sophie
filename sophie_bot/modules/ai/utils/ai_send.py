from __future__ import annotations

import re
from typing import Any, cast

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message, ReplyParameters
from stfu_tg import Doc

from sophie_bot.modules.ai.utils.ai_header import (
    AI_CHATBOT_CUSTOM_EMOJI_ID,
    AI_CUSTOM_EMOJI_ID,
    AI_GENERATING_EMOJI_ID,
)
from sophie_bot.modules.utils_.delivery import deliver_doc

_AI_MARKER = re.compile(
    rf'^(?:<p>)?(<tg-emoji emoji-id="(?:{AI_CUSTOM_EMOJI_ID}|{AI_CHATBOT_CUSTOM_EMOJI_ID}|{AI_GENERATING_EMOJI_ID})">'
    r"[^<]+</tg-emoji>)"
)


def ai_continuation_prefix(doc: Doc) -> str:
    """Repeat the recognized marker on AI continuations, without repeating the header."""
    marker = _AI_MARKER.match(doc.to_rich())
    return marker[1] + " " if marker else ""


def editable_reply_markup(reply_markup: Any) -> InlineKeyboardMarkup | None:
    """Return markup supported by Telegram message edit methods."""
    return reply_markup if isinstance(reply_markup, InlineKeyboardMarkup) else None


async def send_ai_rich_message(message: Message, doc: Doc, **reply_kwargs: Any) -> Message:
    """Send a rich AI reply, falling back to a plain message if its source was deleted."""
    return cast(
        Message,
        await deliver_doc(
            message.bot,
            message.chat.id,
            doc,
            mode="rich",
            continuation_prefix=ai_continuation_prefix(doc),
            allow_deleted_reply=True,
            reply_parameters=ReplyParameters(message_id=message.message_id),
            message_thread_id=message.message_thread_id,
            **reply_kwargs,
        ),
    )


async def send_ai_rich_message_to_chat(
    chat_id: int,
    doc: Doc,
    reply_to_message_id: int | None = None,
    reply_parameters: ReplyParameters | None = None,
    *,
    bot: Bot,
    **send_kwargs: Any,
) -> Message:
    """Send a rich AI message to a chat."""
    if reply_to_message_id is not None and reply_parameters is None:
        reply_parameters = ReplyParameters(message_id=reply_to_message_id)
    if reply_parameters is not None:
        send_kwargs["reply_parameters"] = reply_parameters
    return cast(
        Message,
        await deliver_doc(
            bot, chat_id, doc, mode="rich", continuation_prefix=ai_continuation_prefix(doc), **send_kwargs
        ),
    )
