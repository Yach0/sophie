from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel
from pymongo.errors import DuplicateKeyError


class GroupUserWhitelistModel(Document):
    """A Telegram user exempt from Sophie's automated moderation in one group."""

    chat_tid: int
    user_tid: int
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "group_user_whitelist"
        indexes: ClassVar = [
            IndexModel([("chat_tid", ASCENDING), ("user_tid", ASCENDING)], unique=True),
        ]

    @classmethod
    async def add_user(cls, chat_tid: int, user_tid: int) -> bool:
        membership = {"chat_tid": chat_tid, "user_tid": user_tid}
        if await cls.find_one(membership):
            return False

        try:
            await cls(**membership).insert()
        except DuplicateKeyError:
            return False
        return True

    @classmethod
    async def remove_user(cls, chat_tid: int, user_tid: int) -> bool:
        entry = await cls.find_one({"chat_tid": chat_tid, "user_tid": user_tid})
        if not entry:
            return False
        await entry.delete()
        return True
