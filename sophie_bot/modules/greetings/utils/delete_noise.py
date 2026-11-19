from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from sophie_bot.utils.logger import log

_EXPECTED_DELETION_ERRORS = (
    "message to delete not found",
    "message can't be deleted",
    "chat_admin_required",
    "not enough rights to delete messages",
)


async def delete_noise(bot: Bot, chat_tid: int, message_id: int, *, receiver_user_tid: int | None = None) -> None:
    """Delete one known message, tolerating only missing messages and denied deletion."""
    try:
        if receiver_user_tid is None:
            await bot.delete_message(chat_id=chat_tid, message_id=message_id)
        else:
            await bot.delete_ephemeral_message(
                chat_id=chat_tid, receiver_user_id=receiver_user_tid, ephemeral_message_id=message_id
            )
    except TelegramForbiddenError as error:
        log.debug("Noise cleanup forbidden", chat_tid=chat_tid, message_id=message_id, error=str(error))
    except TelegramBadRequest as error:
        if error.message.lower().removeprefix("bad request: ") not in _EXPECTED_DELETION_ERRORS:
            raise
        log.debug("Noise cleanup unavailable", chat_tid=chat_tid, message_id=message_id, error=str(error))
