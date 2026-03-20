from __future__ import annotations
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject
from sophie_bot.modules.locks.utils.cache import get_cached_locks
from sophie_bot.modules.locks.utils.detect_lock import check_locks
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


class LocksEnforcerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        message: Message = event
        if message.chat.type == ChatType.PRIVATE:
            return await handler(event, data)
        if not message.from_user:
            return await handler(event, data)
        if not await is_enabled("locks"):
            return await handler(event, data)
        if await is_user_admin(message.chat.id, message.from_user.id):
            return await handler(event, data)
        chat_db = data.get("chat_db")
        if not chat_db:
            return await handler(event, data)
        locked_types = await get_cached_locks(message.chat.id, chat_db.iid)
        if not locked_types:
            return await handler(event, data)
        matched_lock = await check_locks(message, locked_types)
        if matched_lock:
            try:
                await message.delete()
            except Exception as e:
                log.debug("Failed to delete locked message", error=str(e))
            raise SkipHandler
        return await handler(event, data)
