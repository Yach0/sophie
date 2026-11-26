from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from beanie import Document, PydanticObjectId
from beanie.odm.queries.update import UpdateResponse
from beanie.operators import Inc, Set
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel
from pymongo.errors import DuplicateKeyError

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class AntispamMode(StrEnum):
    OFF = "off"
    NORMAL = "normal"
    STRICT = "strict"


class AntispamIncidentResolution(StrEnum):
    UNDONE = "undone"
    BANNED = "banned"


DEFAULT_TRUSTED_AFTER_SECONDS = 30 * 86400


class AntispamPolicy(BaseModel):
    mode: AntispamMode = AntispamMode.NORMAL
    third_party_enabled: bool = True
    trusted_after_seconds: int | None = Field(default=DEFAULT_TRUSTED_AFTER_SECONDS, ge=60, le=3650 * 86400)
    mute_duration_seconds: int = Field(default=86400, ge=30, le=366 * 86400)
    notify_chat: bool = True
    scan_new_user_messages_limit: int | None = Field(default=5, ge=0)


class AntispamSettingsModel(Document):
    chat: Link[ChatModel]
    mode: AntispamMode = AntispamMode.NORMAL
    third_party_enabled: bool = True
    trusted_after_seconds: int | None = Field(default=DEFAULT_TRUSTED_AFTER_SECONDS, ge=60, le=3650 * 86400)
    mute_duration_seconds: int = Field(default=86400, ge=30, le=366 * 86400)
    notify_chat: bool = True
    scan_new_user_messages_limit: int | None = Field(default=5, ge=0)
    revision: int = 0

    class Settings:
        name = "antispam_settings"
        indexes: ClassVar = [IndexModel("chat", unique=True, name="antispam_chat_unique")]

    def to_policy(self) -> AntispamPolicy:
        return AntispamPolicy(
            mode=self.mode,
            third_party_enabled=self.third_party_enabled,
            trusted_after_seconds=self.trusted_after_seconds,
            mute_duration_seconds=self.mute_duration_seconds,
            notify_chat=self.notify_chat,
            scan_new_user_messages_limit=self.scan_new_user_messages_limit,
        )

    @classmethod
    async def for_chat(cls, chat: ChatModel) -> AntispamSettingsModel | None:
        return await cls.find_one(cls.chat.id == chat.iid)

    @classmethod
    async def get_policy(cls, chat: ChatModel) -> AntispamPolicy:
        current = await cls.for_chat(chat)
        return current.to_policy() if current is not None else AntispamPolicy()

    @classmethod
    async def update_policy(
        cls,
        chat: ChatModel,
        change: Callable[[AntispamPolicy], AntispamPolicy],
    ) -> AntispamSettingsModel:
        while True:
            current = await cls.for_chat(chat)
            policy = change(current.to_policy() if current is not None else AntispamPolicy())
            values = policy.model_dump(mode="python")
            if current is None:
                try:
                    return await cls(chat=chat, **values).insert()
                except DuplicateKeyError:
                    continue
            condition = (
                {"$or": [{"revision": 0}, {"revision": {"$exists": False}}]}
                if current.revision == 0
                else {"revision": current.revision}
            )
            updated = await cls.find_one(cls.chat.id == chat.iid, condition).update(
                Set(values),
                Inc({cls.revision: 1}),
                response_type=UpdateResponse.NEW_DOCUMENT,
            )
            if updated is not None:
                return updated


class AntispamIncidentModel(Document):
    """Tracks an enforced anti-spam action, its messages scheduled for deletion, and undo/ban resolution."""

    chat: Link[ChatModel]
    chat_tid: int
    user_tid: int
    user_name: str
    source_id: str
    source_name: str
    mode: AntispamMode
    user_message_id: int | None = None
    original_message_text: str | None = None
    notice_message_id: int | None = None
    delete_at: datetime | None = None
    user_message_deleted_at: datetime | None = None
    notice_deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolution: AntispamIncidentResolution | None = None
    resolved_at: datetime | None = None
    resolved_by_tid: int | None = None

    class Settings:
        name = "antispam_incidents"
        indexes: ClassVar = [
            IndexModel(
                [("delete_at", ASCENDING), ("notice_deleted_at", ASCENDING), ("user_message_deleted_at", ASCENDING)],
                name="antispam_cleanup_due",
            ),
            IndexModel(
                [("chat.$id", ASCENDING), ("user_tid", ASCENDING)],
                name="antispam_incident_chat_user",
            ),
        ]


class AntispamExclusionModel(Document):
    """Per-chat anti-spam exclusions created by undoing actions, with USSR text for post-training."""

    chat: Link[ChatModel]
    chat_tid: int
    user_tid: int
    source_id: str
    original_message_text: str | None = None
    incident_id: PydanticObjectId | None = None
    created_by_tid: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "antispam_exclusions"
        indexes: ClassVar = [
            IndexModel(
                [("chat.$id", ASCENDING), ("user_tid", ASCENDING)],
                unique=True,
                name="antispam_exclusion_chat_user_unique",
            ),
            IndexModel(
                [("source_id", ASCENDING), ("created_at", ASCENDING)],
                name="antispam_exclusion_source_time",
            ),
        ]

    @classmethod
    async def is_excluded(cls, chat_iid: PydanticObjectId, user_tid: int) -> bool:
        return await cls.find_one(cls.chat.id == chat_iid, cls.user_tid == user_tid) is not None

    @classmethod
    async def record(
        cls,
        chat: ChatModel,
        user_tid: int,
        *,
        source_id: str,
        created_by_tid: int,
        original_message_text: str | None = None,
        incident_id: PydanticObjectId | None = None,
    ) -> AntispamExclusionModel:
        now = datetime.now(UTC)
        update_fields = {
            "chat_tid": chat.tid,
            "source_id": source_id,
            "original_message_text": original_message_text,
            "incident_id": incident_id,
            "created_by_tid": created_by_tid,
            "created_at": now,
        }
        return await cls.find_one(cls.chat.id == chat.iid, cls.user_tid == user_tid).upsert(
            Set(update_fields),
            on_insert=cls(
                chat=chat,
                user_tid=user_tid,
                **update_fields,
            ),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )


class AntispamConfirmedSpamModel(Document):
    """Admin-confirmed spam messages saved for post-training when a mute is escalated to a ban."""

    chat: Link[ChatModel]
    chat_tid: int
    user_tid: int
    source_id: str
    message_text: str
    incident_id: PydanticObjectId | None = None
    confirmed_by_tid: int
    message_timestamp: datetime
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "antispam_confirmed_spam"
        indexes: ClassVar = [
            IndexModel(
                [("source_id", ASCENDING), ("confirmed_at", ASCENDING)],
                name="antispam_confirmed_spam_source_time",
            ),
            IndexModel(
                [("chat.$id", ASCENDING), ("confirmed_at", ASCENDING)],
                name="antispam_confirmed_spam_chat_time",
            ),
        ]
