from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputRichMessage, Message, ReplyParameters
from stfu_tg import Doc

from sophie_bot.modules.ai.utils.rich_delivery import with_photo_url_fallback
from sophie_bot.modules.utils_.telegram_exceptions import REPLIED_NOT_FOUND, REPLY_MESSAGE_INVALID


def editable_reply_markup(reply_markup: Any) -> InlineKeyboardMarkup | None:
    """Return markup supported by Telegram message edit methods."""
    return reply_markup if isinstance(reply_markup, InlineKeyboardMarkup) else None


async def send_ai_rich_message(message: Message, doc: Doc, **reply_kwargs: Any) -> Message:
    """Send a rich AI reply, falling back to a plain message if its source was deleted."""

    async def send(rich_message: InputRichMessage) -> Message:
        try:
            return await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
                chat_id=message.chat.id,
                rich_message=rich_message,
                reply_parameters=ReplyParameters(message_id=message.message_id),
                message_thread_id=message.message_thread_id,
                **reply_kwargs,
            )
        except TelegramBadRequest as error:
            if not any(error_text in error.message for error_text in (REPLIED_NOT_FOUND, REPLY_MESSAGE_INVALID)):
                raise
            return await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
                chat_id=message.chat.id,
                rich_message=rich_message,
                message_thread_id=message.message_thread_id,
                **reply_kwargs,
            )

    return await with_photo_url_fallback(doc.to_rich(), send)


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
    return await with_photo_url_fallback(
        doc.to_rich(),
        lambda rich_message: bot.send_rich_message(chat_id=chat_id, rich_message=rich_message, **send_kwargs),
    )
