from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.modules.utils_.admin import ensure_admin_snapshot
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.logger import log


class AdmincacheMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        services: ApplicationServices = data["services"]
        context: RequestContext = data["context"]
        chat = context.event_chat
        if chat is None:
            log.debug("AdmincacheMiddleware: No event chat available, skipping")
        elif chat.tid > 0:
            log.debug("AdmincacheMiddleware: Not a group chat, skipping", chat_id=chat.tid)
        else:
            await ensure_admin_snapshot(
                chat,
                bot=services.bot,
                redis=services.redis,
            )
        return await handler(event, data)
