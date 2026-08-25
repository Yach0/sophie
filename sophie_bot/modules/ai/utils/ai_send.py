from __future__ import annotations

from typing import Any

from aiogram.types import InputRichMessage, Message, ReplyParameters
from stfu_tg import Doc

from sophie_bot.services.bot import bot


async def send_ai_rich_message(message: Message, doc: Doc, **reply_kwargs: Any) -> Message:
    """Reply with a rich message.

    Everything the AI sends goes through here so its header renders as a table and users can reply
    to it, which is how a reply continues the conversation.
    """
    return await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
        chat_id=message.chat.id,
        rich_message=InputRichMessage(html=doc.to_rich()),
        reply_parameters=ReplyParameters(message_id=message.message_id),
        message_thread_id=message.message_thread_id,
        **reply_kwargs,
    )


async def send_ai_rich_message_to_chat(chat_id: int, doc: Doc, **send_kwargs: Any) -> Message:
    """Send a rich AI message to a chat."""
    return await bot.send_rich_message(
        chat_id=chat_id,
        rich_message=InputRichMessage(html=doc.to_rich()),
        **send_kwargs,
    )
