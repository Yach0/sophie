import sys
from typing import Any

from aiogram.handlers import ErrorHandler
from aiogram.types import Chat, Update

from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.error.utils.backoff import compute_error_signature, should_notify
from sophie_bot.modules.error.utils.capture import capture_sentry
from sophie_bot.modules.error.utils.error_message import generic_error_message
from sophie_bot.modules.error.utils.ignored import QUIET_EXCEPTIONS
from sophie_bot.modules.error.utils.permission_errors import (
    handle_no_rights_error,
    is_no_rights_error,
)
from sophie_bot.services.logfire import capture_logfire_error
from sophie_bot.utils.exception import SophieException
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
        logfire_trace_id = self.capture_logfire(exception)
        self.log_to_console(etype, value, tb, sentry_event_id=sentry_event_id, logfire_trace_id=logfire_trace_id)

        if not isinstance(sys_exception, Exception):
            log.warning("No sys exception", from_aiogram=exception, from_sys=sys_exception)
            return

        if exception != sys_exception:
            log.warning(
                "Mismatched exception seeking",
                from_aiogram=exception,
                from_sys=sys_exception,
            )

        # Try to reset state
        try:
            await self.data["state"].clear()
        except Exception as err:  # noqa: BLE001  # best-effort state reset in top-level error handler
            log.warning("Failed to clear state", err=err)

        if update.inline_query:
            return  # Do not send messages after inline query

        chat: Chat = self.data["event_chat"]

        # Global exponential backoff via Redis: suppress repeated crash notifications
        signature = compute_error_signature(sys_exception)
        notify = await should_notify(signature, redis=self.data["services"].redis)
        if not notify:
            log.info("Suppressing error notification", signature=signature)
            return

        await self.bot.send_message(
            chat.id,
            **generic_error_message(sys_exception, sentry_event_id, logfire_trace_id=logfire_trace_id),
        )

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
    def _exception_to_report(exception: Exception) -> Exception:
        sys_exc = sys.exception()
        if isinstance(sys_exc, Exception):
            exception = sys_exc
        cause = exception.__cause__
        if isinstance(exception, AIRequestFailed) and isinstance(cause, Exception):
            return cause
        return exception

    @staticmethod
    def capture_sentry(exception: Exception) -> str | None:
        # The AI path already captured the provider failure with its model and operation.
        if isinstance(exception, SophieException) and exception.sentry_event_id:
            return exception.sentry_event_id
        return capture_sentry(SophieErrorHandler._exception_to_report(exception))

    @staticmethod
    def capture_logfire(exception: Exception) -> str | None:
        if isinstance(exception, AIRequestFailed) and exception.logfire_trace_id:
            return exception.logfire_trace_id
        return capture_logfire_error(SophieErrorHandler._exception_to_report(exception))
