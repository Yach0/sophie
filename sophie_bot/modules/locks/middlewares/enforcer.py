from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from sophie_bot.modules.locks.utils.cache import get_cached_locks
from sophie_bot.modules.locks.utils.detect_lock import check_locks
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted
from sophie_bot.utils.logger import log


class LocksEnforcerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        message: Message = event
        if message.chat.type == ChatType.PRIVATE:
            return await handler(event, data)
        if not message.from_user:
            return await handler(event, data)
        chat_db = data.get("chat_db")
        if not chat_db:
            return await handler(event, data)
        if not await is_enabled("locks", chat_tid=message.chat.id):
            return await handler(event, data)
        if await is_user_globally_whitelisted(message.from_user.id) or await is_user_admin(
            message.chat.id, message.from_user.id
        ):
            return await handler(event, data)
        locked_types = await get_cached_locks(message.chat.id, chat_db.iid)
        if not locked_types:
            return await handler(event, data)
        # When the media-group middleware aggregated an album, `message` is only the
        # representative item. Check locks against every album message so a locked later
        # item can't slip through when the first item is allowed, and delete the whole
        # album when any item matches.
        album: list[Message] = data.get("album") or [message]
        matched_lock = None
        for candidate in album:
            matched_lock = await check_locks(candidate, locked_types)
            if matched_lock:
                break

        if matched_lock:
            for locked_message in album:
                try:
                    await locked_message.delete()
                except Exception as exc:  # noqa: BLE001  # best-effort delete of locked album message
                    log.debug("Failed to delete locked message", error=str(exc))
            raise SkipHandler
        return await handler(event, data)
