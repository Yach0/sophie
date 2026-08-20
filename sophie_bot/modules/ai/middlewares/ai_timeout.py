import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, AIRequestFailed, capture_ai_error
from sophie_bot.utils.i18n import gettext as _

_TIMEOUT_CONTEXT = AIErrorContext(operation="handler_timeout")


class AiTimeoutMiddleware(BaseMiddleware):
    """Prevents AI handlers from hanging indefinitely by wrapping them with asyncio.wait_for."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        status = get_flag(data, "status", default=None)
        if not status:
            return await handler(event, data)

        try:
            return await asyncio.wait_for(
                handler(event, data),
                timeout=CONFIG.ai_timeout_seconds,
            )
        except TimeoutError as error:
            raise AIRequestFailed(
                capture_ai_error(error, _TIMEOUT_CONTEXT),
                _("The AI request took too long and was cancelled. Please try again."),
            ) from error
