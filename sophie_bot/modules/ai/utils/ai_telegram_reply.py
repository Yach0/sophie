from __future__ import annotations

from aiogram.types import Message, ReplyKeyboardMarkup

from sophie_bot.db.models import ChatModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_chatbot_reply import ai_chatbot_reply


async def new_ai_reply(message: Message, markup: ReplyKeyboardMarkup | None = None) -> Message:
    """
    Generate an AI reply and send it as a Telegram message.
    """
    chat_db = await ChatModel.get_by_tid(message.chat.id)
    if not chat_db:
        raise ValueError("Chat not found in database")

    connection = ChatConnection(
        type=chat_db.type,
        is_connected=False,
        tid=chat_db.tid,
        title=chat_db.first_name_or_title,
        db_model=chat_db,
    )

    return await ai_chatbot_reply(message, connection, user_text=None, reply_markup=markup)
