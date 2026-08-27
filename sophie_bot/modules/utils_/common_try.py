from collections.abc import Callable, Coroutine
from typing import Any

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNotFound,
)
from aiogram.methods import TelegramMethod

from sophie_bot.modules.utils_.telegram_exceptions import (
    CAN_NOT_BE_DELETED,
    CHAT_WRITE_FORBIDDEN,
    INVALID_BUTTON_URL,
    MSG_NOT_MODIFIED,
    MSG_TEXT_EMPTY,
    MSG_TO_DEL_NOT_FOUND,
    MSG_TO_EDIT_NOT_FOUND,
    MSG_TOO_LONG,
    NO_TEXT_IN_MSG_TO_EDIT,
    REPLIED_NOT_FOUND,
    REPLY_MESSAGE_INVALID,
)
from sophie_bot.utils.logger import log

COROUTINE_TYPE = Coroutine[Any, Any, Any] | TelegramMethod
CALLBACK_COROUTINE_TYPE = Callable[[], COROUTINE_TYPE]
IGNORED_EXCEPTIONS = (TelegramNotFound, TelegramForbiddenError, TelegramMigrateToChat)


_REPLY_NOT_FOUND_ERRORS = (REPLIED_NOT_FOUND, REPLY_MESSAGE_INVALID)
_IGNORED_BAD_REQUEST_ERRORS = (
    *_REPLY_NOT_FOUND_ERRORS,
    CAN_NOT_BE_DELETED,
    MSG_TO_DEL_NOT_FOUND,
    MSG_TEXT_EMPTY,
    MSG_NOT_MODIFIED,
    NO_TEXT_IN_MSG_TO_EDIT,
    MSG_TOO_LONG,
    INVALID_BUTTON_URL,
    CHAT_WRITE_FORBIDDEN,
)

async def common_try(
    to_try: COROUTINE_TYPE,
    reply_not_found: CALLBACK_COROUTINE_TYPE | None = None,
    edit_not_found: CALLBACK_COROUTINE_TYPE | None = None,
) -> Any:
    """
    Catches common Telegram exceptions
    """
    try:
        log.debug("common_try: Trying to execute callback")
        return await to_try
    except TelegramBadRequest as err:
        if reply_not_found and any(err_text in err.message for err_text in _REPLY_NOT_FOUND_ERRORS):
            log.debug("common_try: Reply not found, trying to execute reply_not_found")
            return await common_try(to_try=reply_not_found())
        if edit_not_found and MSG_TO_EDIT_NOT_FOUND in err.message:
            log.debug("common_try: Message to edit not found, trying to execute edit_not_found")
            return await common_try(to_try=edit_not_found())
        if any(err_text in err.message for err_text in _IGNORED_BAD_REQUEST_ERRORS):
            log.debug("common_try: Ignored expected bad request", error=str(err))
            return None
        log.warning("common_try: Unknown TelegramBadRequest exception, re-raising", error=str(err))
        raise
    except IGNORED_EXCEPTIONS as err:
        log.warning("common_try: Caught ignored exception", error=str(err))
        return None
    except TelegramAPIError as err:
        err_str = str(err).lower()
        if "timeout" in err_str:
            log.warning("common_try: Telegram API timeout", error=str(err))
            raise
        log.warning("common_try: Other unhandled Telegram API error", error=str(err))
        raise
