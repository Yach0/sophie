from __future__ import annotations


from beanie import Document, PydanticObjectId
from pydantic import Field

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class LocksModel(Document):
    chat: Link[ChatModel]
    locked_types: set[str] = Field(default_factory=set)

    class Settings:
        name = "locks"

    @staticmethod
    async def get_by_chat_iid(chat_iid: PydanticObjectId) -> LocksModel:
        existing = await LocksModel.find_one(LocksModel.chat.id == chat_iid)
        if existing:
            return existing
        return LocksModel(chat=chat_iid)

    async def lock(self, lock_type: str) -> bool:
        if lock_type in self.locked_types:
            return False
        self.locked_types = self.locked_types | {lock_type}
        await self.save()
        return True

    async def unlock(self, lock_type: str) -> bool:
        if lock_type not in self.locked_types:
            return False
        self.locked_types = self.locked_types - {lock_type}
        await self.save()
        return True

    async def lock_multiple(self, lock_types: set[str]) -> int:
        new_locks = lock_types - self.locked_types
        if not new_locks:
            return 0
        self.locked_types = self.locked_types | new_locks
        await self.save()
        return len(new_locks)

    async def unlock_multiple(self, lock_types: set[str]) -> int:
        removed = self.locked_types & lock_types
        if not removed:
            return 0
        self.locked_types = self.locked_types - lock_types
        await self.save()
        return len(removed)

    @staticmethod
    async def get_locked_types(chat_iid: PydanticObjectId) -> set[str]:
        model = await LocksModel.find_one(LocksModel.chat.id == chat_iid)
        if model:
            return model.locked_types
        return set()
