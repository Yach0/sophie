from __future__ import annotations

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.modules.utils_.admin import get_admin_record
from sophie_bot.utils.cached import AsyncLookupCache


class ChatLookupCache:
    """A caller-owned snapshot for display-context lookups, not authorization checks."""

    def __init__(self) -> None:
        self._chats: AsyncLookupCache[int, ChatModel | None] = AsyncLookupCache(ChatModel.get_by_tid)
        self._admins: AsyncLookupCache[tuple[int, int], ChatAdminModel | None] = AsyncLookupCache(
            self._load_admin_record
        )

    async def get_chat_by_tid(self, chat_tid: int) -> ChatModel | None:
        return await self._chats.get(chat_tid)

    async def get_admin_record(self, chat_tid: int, user_tid: int) -> ChatAdminModel | None:
        return await self._admins.get((chat_tid, user_tid))

    async def _load_admin_record(self, key: tuple[int, int]) -> ChatAdminModel | None:
        chat_tid, user_tid = key
        chat_model = await self.get_chat_by_tid(chat_tid)
        user_model = await self.get_chat_by_tid(user_tid)
        if chat_model is None or user_model is None:
            return None
        return await get_admin_record(chat_model, user_model)
