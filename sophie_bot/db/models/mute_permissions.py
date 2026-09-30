from __future__ import annotations

from typing import ClassVar

from aiogram.types import ChatPermissions
from beanie import Document
from pymongo import ASCENDING, IndexModel


class MutePermissionsModel(Document):
    """Permissions captured before Sophie applies a mute in a chat."""

    chat_tid: int
    user_tid: int
    permissions: ChatPermissions
    applied: bool = False

    class Settings:
        name = "mute_permissions"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel([("chat_tid", ASCENDING), ("user_tid", ASCENDING)], unique=True),
        ]
