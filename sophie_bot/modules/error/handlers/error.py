import sys
from typing import Any, Optional

from aiogram.handlers import ErrorHandler
from aiogram.types import Chat, Update

from sophie_bot.modules.error.utils.capture import capture_sentry
from sophie_bot.modules.error.utils.ignored import QUIET_EXCEPTIONS
from sophie_bot.modules.error.utils.error_message import generic_error_message
from sophie_bot.modules.error.utils.backoff import compute_error_signature, should_notify
from sophie_bot.modules.error.utils.permission_errors import (
    handle_no_rights_error,
    is_no_rights_error,
)
from sophie_bot.utils.logger import log


class SophieErrorHandler(ErrorHandler):
    async def handle(self) -> Any:
        # We are ignoring the type because I'm sure that aiogram will have this field
        exception = self.event.exception  # type: ignore
        update: Update = self.event.update  # type: ignore

        if isinstance(exception, QUIET_EXCEPTIONS):
            return

        # Check for permission errors that indicate we should leave the chat
        if is_no_rights_error(exception):
            chat = self.data.get("event_chat")
            await handle_no_rights_error(self.bot, chat, exception)
            return

        etype, value, tb = sys.exc_info()

        sys_exception = sys.exception()

        sentry_event_id = self.capture_sentry(exception)
        self.log_to_console(etype, value, tb, sentry_event_id=sentry_event_id)

        if not sys_exception:
            log.warning("No sys exception", from_aiogram=exception, from_sys=sys_exception)
            return

        elif exception != sys_exception:
            log.warning(
                "Mismatched exception seeking",
                from_aiogram=exception,
                from_sys=sys_exception,
            )

        # Try to reset state
        try:
            await self.data["state"].clear()
        except Exception as err:
            log.warning("Failed to clear state", err=err)

        if update.inline_query:
            return  # Do not send messages after inline query

        chat: Chat = self.data["event_chat"]

        # Global exponential backoff via Redis: suppress repeated crash notifications
        signature = compute_error_signature(sys_exception)
        notify = await should_notify(signature)
        if not notify:
            log.info("Suppressing error notification", signature=signature)
            return

        # Pyright doesn't know that we are returning out of the function if there's no sys_exception
        await self.bot.send_message(chat.id, **generic_error_message(sys_exception, sentry_event_id))  # type: ignore

    @staticmethod
    def log_to_console(etype, value, tb, **kwargs):
        if etype and value and tb:
            # Ensure traceback is attached for Sentry logging integration
            log.warning("Unhandled exception", exc_info=(etype, value, tb))
        else:
            # Fallback: no sys exc_info available
            log.warning("Unhandled exception (no sys exc_info available)")
        if kwargs:
            log.warning("Additional error data", **kwargs)

    @staticmethod
    def capture_sentry(exception: Exception) -> Optional[str]:
        # Prefer capturing the active system exception to preserve full traceback
        sys_exc = sys.exception()
        if isinstance(sys_exc, Exception):
            return capture_sentry(sys_exc)
        return capture_sentry(exception)
