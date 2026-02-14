"""Module for handling permission-related errors and automatic chat leaving.

This module provides utilities to detect when Sophie lacks permissions in a chat
and handle the situation gracefully by leaving the chat and logging the event.
"""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat

from sophie_bot.db.models import ChatLeaveLogModel, ChatModel
from sophie_bot.utils.logger import log


def is_no_rights_error(exception: Exception) -> bool:
    """Check if the exception is a 'not enough rights' error.

    Args:
        exception: The exception to check.

    Returns:
        True if the exception indicates missing rights to send messages.
    """
    if not isinstance(exception, TelegramBadRequest):
        return False
    error_message = str(exception).lower()
    return "not enough rights" in error_message and "send" in error_message


async def handle_no_rights_error(
    bot,
    chat: Chat | None,
    exception: TelegramBadRequest,
) -> bool:
    """Handle 'not enough rights' error by leaving the chat and logging it.

    Args:
        bot: The aiogram bot instance.
        chat: The chat object from event data, if available.
        exception: The TelegramBadRequest exception that triggered this.

    Returns:
        True if the error was handled successfully, False otherwise.
    """
    if not isinstance(chat, Chat):
        log.warning("Cannot handle no-rights error: no event_chat in data")
        return False

    chat_tid = chat.id
    log.warning(
        "Bot lacks rights to send messages, leaving chat",
        chat_id=chat_tid,
        chat_title=getattr(chat, "title", "Unknown"),
        error=str(exception),
    )

    # Try to leave the chat
    try:
        await bot.leave_chat(chat_tid)
        log.info("Successfully left chat due to permission restrictions", chat_id=chat_tid)
    except Exception as leave_error:
        log.error("Failed to leave chat", chat_id=chat_tid, error=str(leave_error))

    # Get the chat model and log the leave event
    chat_model = await ChatModel.get_by_tid(chat_tid)
    if chat_model:
        # Log the forced leave
        leave_log = ChatLeaveLogModel(
            chat=chat_model,
            reason="missing_send_permissions",
            error_message=str(exception),
        )
        await leave_log.save()
        log.info("Logged forced chat leave", chat_id=chat_tid, log_id=str(leave_log.id))
    else:
        log.warning("Chat model not found for logging forced leave", chat_id=chat_tid)

    return True
