from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from aiogram.types import ChatPermissions
from beanie import Document
from pydantic import field_validator
from pymongo import ASCENDING, IndexModel


class MutePermissionsModel(Document):
    """Permissions captured before Sophie applies a mute in a chat."""

    chat_tid: int
    user_tid: int
    permissions: ChatPermissions | None = None
    restore_group_defaults: bool = False
    applied_permissions: ChatPermissions | None = None
    pending_permissions: ChatPermissions | None = None
    applied: bool = False
    previous_until: datetime | None = None
    applied_until: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("previous_until", "applied_until", "expires_at")
    @classmethod
    def utc_datetime(cls, value: datetime | None) -> datetime | None:
        # PyMongo's default codec returns naive UTC dates.
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value

    class Settings:
        name = "mute_permissions"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel([("chat_tid", ASCENDING), ("user_tid", ASCENDING)], unique=True),
            IndexModel("expires_at"),
        ]


class MutePermissionsLockModel(Document):
    """Separate leases keep serializing operations when a snapshot is removed."""

    class Settings:
        name = "mute_permission_locks"
        indexes: ClassVar[list[IndexModel]] = [IndexModel("lock_until", expireAfterSeconds=0)]
