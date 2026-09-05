from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from beanie import Document, Indexed
from pydantic import Field
from pymongo.errors import DuplicateKeyError


class GlobalUserWhitelistModel(Document):
    """A Telegram user exempt from Sophie's automated moderation in every chat."""

    user_tid: Annotated[int, Indexed(unique=True)]
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "global_user_whitelist"

    @classmethod
    async def add_user(cls, user_tid: int) -> bool:
        if await cls.find_one(cls.user_tid == user_tid):
            return False

        try:
            await cls(user_tid=user_tid).insert()
        except DuplicateKeyError:
            return False
        return True

    @classmethod
    async def remove_user(cls, user_tid: int) -> bool:
        entry = await cls.find_one(cls.user_tid == user_tid)
        if not entry:
            return False
        await entry.delete()
        return True
