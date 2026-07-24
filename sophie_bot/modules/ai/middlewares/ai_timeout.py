import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.utils.exception import SophieException


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
        except TimeoutError:
            raise SophieException("AI request timed out. Please try again.")
