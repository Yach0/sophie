from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from beanie import PydanticObjectId

from sophie_bot.constants import CACHE_ADMIN_TTL_SECONDS
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.modules.utils_.chat_member import update_chat_members
from sophie_bot.utils.logger import log


class AdmincacheMiddleware(BaseMiddleware):
    """Middleware that automatically pulls admins from the database and refreshes cache if needed.

    This middleware ensures that chat admins are available in the database for subsequent
    permission checks. It pulls admins from the cache (ChatAdminModel) and fetches fresh
    data from Telegram if the cache is missing or too old.

    The cache TTL is determined by CACHE_DEFAULT_TTL_SECONDS from constants (30 minutes).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        await self._refresh_cache_if_needed(event, data)

        return await handler(event, data)

    async def _refresh_cache_if_needed(self, event: Update, data: dict[str, Any]) -> None:
        """Check if admin cache needs refresh and fetch from Telegram if so."""
        chat_db = data.get("group_db") or data.get("chat_db")
        if not chat_db:
            log.debug("AdmincacheMiddleware: No chat_db available, skipping")
            return

        chat_tid = getattr(chat_db, "tid", None)
        if not chat_tid or chat_tid > 0:
            log.debug("AdmincacheMiddleware: Not a group chat, skipping", chat_id=chat_tid)
            return

        chat_iid = getattr(chat_db, "iid", None)
        if not chat_iid:
            log.debug("AdmincacheMiddleware: Missing chat_iid, skipping")
            return

        if await self._is_cache_stale(chat_iid):
            log.debug("AdmincacheMiddleware: Refreshing admin cache", chat_id=chat_tid)
            try:
                await update_chat_members(chat_db)
            except Exception as e:
                log.warning("AdmincacheMiddleware: Failed to refresh admin cache", chat_id=chat_tid, error=str(e))
        else:
            log.debug("AdmincacheMiddleware: Admin cache is up to date", chat_id=chat_tid)

    async def _is_cache_stale(self, chat_iid: PydanticObjectId) -> bool:
        """Check if the admin cache is missing or stale.

        Returns True if:
        - No admin entries exist for this chat
        - Any admin entry is older than CACHE_DEFAULT_TTL_SECONDS
        """
        oldest_admin = await self._get_oldest_admin(chat_iid)

        if not oldest_admin:
            return True

        cache_age = (datetime.now() - oldest_admin.last_updated).total_seconds()
        return cache_age > CACHE_ADMIN_TTL_SECONDS

    async def _get_oldest_admin(self, chat_iid: PydanticObjectId) -> ChatAdminModel | None:
        """Get the oldest admin entry for a chat."""
        return await (
            ChatAdminModel.find(ChatAdminModel.chat.id == chat_iid).sort(ChatAdminModel.last_updated).first_or_none()
        )
