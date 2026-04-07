import asyncio
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.utils.exception import SophieException


class AiTimeoutMiddleware(BaseMiddleware):
    """Prevents AI handlers from hanging indefinitely by wrapping them with asyncio.wait_for."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        status = get_flag(data, "status", default=None)
        if not status:
            return await handler(event, data)

        try:
            return await asyncio.wait_for(
                handler(event, data),
                timeout=CONFIG.ai_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise SophieException("AI request timed out. Please try again.")
