from typing import Any, Callable, Coroutine, Optional

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNotFound,
)
from aiogram.methods import TelegramMethod

from sophie_bot.modules.utils_.telegram_exceptions import (
    BOTS_CANT_ADD_NEW_CHAT_MEMBERS,
    CAN_NOT_BE_DELETED,
    CHAT_WRITE_FORBIDDEN,
    HIDE_REQUESTER_MISSING,
    INVALID_BUTTON_URL,
    MSG_NOT_MODIFIED,
    MSG_TEXT_EMPTY,
    MSG_TO_DEL_NOT_FOUND,
    MSG_TOO_LONG,
    NOT_ENOUGH_RIGHTS,
    NO_TEXT_IN_MSG_TO_EDIT,
    REPLIED_NOT_FOUND,
    RIGHT_FORBIDDEN,
    USER_ALREADY_PARTICIPANT,
)
from sophie_bot.utils.logger import log

COROUTINE_TYPE = Coroutine[Any, Any, Any] | TelegramMethod
CALLBACK_COROUTINE_TYPE = Callable[[], COROUTINE_TYPE]
IGNORED_EXCEPTIONS = (TelegramNotFound, TelegramForbiddenError, TelegramMigrateToChat)


async def common_try(to_try: COROUTINE_TYPE, reply_not_found: Optional[CALLBACK_COROUTINE_TYPE] = None) -> Any:
    """
    Catches common Telegram exceptions
    """
    try:
        log.debug("common_try: Trying to execute callback")
        return await to_try
    except TelegramBadRequest as err:
        if reply_not_found and REPLIED_NOT_FOUND in err.message:
            log.debug("common_try: Reply not found, trying to execute reply_not_found")
            return await common_try(to_try=reply_not_found())
        if REPLIED_NOT_FOUND in err.message:
            log.debug("common_try: Reply not found, ignoring")
            return None
        if CAN_NOT_BE_DELETED in err.message:
            log.debug("common_try: Message can't be deleted, ignoring")
            return None
        if MSG_TO_DEL_NOT_FOUND in err.message:
            log.debug("common_try: Message to delete not found, ignoring")
            return None
        if MSG_TEXT_EMPTY in err.message:
            log.debug("common_try: Message text is empty, ignoring")
            return None
        if MSG_NOT_MODIFIED in err.message:
            log.debug("common_try: Message is not modified, ignoring")
            return None
        if NO_TEXT_IN_MSG_TO_EDIT in err.message:
            log.debug("common_try: No text in message to edit, ignoring")
            return None
        if MSG_TOO_LONG in err.message:
            log.warning("common_try: Message is too long, ignoring")
            return None
        if INVALID_BUTTON_URL in err.message:
            log.warning("common_try: Invalid inline keyboard button URL, ignoring", error=str(err))
            return None
        if CHAT_WRITE_FORBIDDEN in err.message:
            log.debug("common_try: Chat write forbidden, ignoring")
            return None
        if RIGHT_FORBIDDEN in err.message:
            log.debug("common_try: Right forbidden, ignoring")
            return None
        if NOT_ENOUGH_RIGHTS in err.message:
            log.debug("common_try: Not enough rights, ignoring")
            return None
        if BOTS_CANT_ADD_NEW_CHAT_MEMBERS in err.message:
            log.debug("common_try: Bots can't add new chat members, ignoring")
            return None
        if USER_ALREADY_PARTICIPANT in err.message:
            log.debug("common_try: User already participant, ignoring")
            return None
        if HIDE_REQUESTER_MISSING in err.message:
            log.debug("common_try: Hide requester missing, ignoring")
            return None
        log.warning("common_try: Unknown TelegramBadRequest exception, re-raising", error=str(err))
        raise err
    except IGNORED_EXCEPTIONS as err:
        log.warning("common_try: Caught ignored exception", error=str(err))
        return None
    except TelegramAPIError as err:
        err_str = str(err).lower()
        if "timeout" in err_str:
            log.warning("common_try: Telegram API timeout", error=str(err))
            raise
        log.warning("common_try: Other unhandled Telegram API error", error=str(err))
        raise err
