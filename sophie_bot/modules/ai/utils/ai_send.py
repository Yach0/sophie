from __future__ import annotations

from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message, ReplyParameters
from stfu_tg import Doc

from sophie_bot.modules.utils_.telegram_exceptions import REPLIED_NOT_FOUND, REPLY_MESSAGE_INVALID
from sophie_bot.services.bot import bot


async def send_ai_rich_message(message: Message, doc: Doc, **reply_kwargs: Any) -> Message:
    """Reply with a rich message.

    Everything the AI sends goes through here so its header renders as a table and users can reply
    to it, which is how a reply continues the conversation.
    """
    # Bot API returns reply keyboards as ``rich_message.blocks`` entries, but aiogram 3.31
    # cannot validate that response shape. Send the keyboard-bearing reply through the regular API.
    if reply_kwargs.get("reply_markup") is not None:
        return await message.reply(doc.to_html(), disable_web_page_preview=True, **reply_kwargs)

    try:
        return await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
            chat_id=message.chat.id,
            rich_message=InputRichMessage(html=doc.to_rich()),
            reply_parameters=ReplyParameters(message_id=message.message_id),
            message_thread_id=message.message_thread_id,
            **reply_kwargs,
        )
    except TelegramBadRequest as err:
        if REPLIED_NOT_FOUND in err.message or REPLY_MESSAGE_INVALID in err.message:
            return await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
                chat_id=message.chat.id,
                rich_message=InputRichMessage(html=doc.to_rich()),
                message_thread_id=message.message_thread_id,
                **reply_kwargs,
            )
        raise


async def send_ai_rich_message_to_chat(
    chat_id: int,
    doc: Doc,
    reply_to_message_id: int | None = None,
    reply_parameters: ReplyParameters | None = None,
    **send_kwargs: Any,
) -> Message:
    """Send a rich AI message to a chat."""
    if reply_to_message_id is not None and reply_parameters is None:
        reply_parameters = ReplyParameters(message_id=reply_to_message_id)
    elif "reply_to_message_id" in send_kwargs and reply_parameters is None:
        reply_parameters = ReplyParameters(message_id=send_kwargs.pop("reply_to_message_id"))
    if reply_parameters is not None:
        send_kwargs["reply_parameters"] = reply_parameters
    return await bot.send_rich_message(
        chat_id=chat_id,
        rich_message=InputRichMessage(html=doc.to_rich()),
        **send_kwargs,
    )
