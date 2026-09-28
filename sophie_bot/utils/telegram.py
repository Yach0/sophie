from __future__ import annotations

from aiogram.types import Message


def is_bot_authored_message(message: Message) -> bool:
    """Return whether a message was authored directly by a Telegram bot user."""
    return message.sender_chat is None and message.from_user is not None and message.from_user.is_bot
