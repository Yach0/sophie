from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from beanie import Document, PydanticObjectId, UpdateResponse
from beanie.odm.operators.update.general import Set
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.communities_enums import CommunityTaskType
from sophie_bot.db.models.federations_enums import TaskStatus


class CommunityModel(Document):
    """Registry of Telegram communities Sophie has observed.

    Populated by ``SaveChatsMiddleware`` from ``community_chat_added`` service
    messages (and lazy ``getChat`` backfill), mirroring ``ChatTopicModel``. The
    chat→community membership edge lives on ``ChatModel.community_tid``.
    """

    community_tid: int
    name: str | None = None
    first_saw: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_saw: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "communities"
        indexes: ClassVar = [
            IndexModel([("community_tid", ASCENDING)], unique=True),
        ]

    @staticmethod
    async def ensure_community(community_tid: int, name: str | None) -> CommunityModel:
        now = datetime.now(UTC)
        set_data: dict = {CommunityModel.last_saw: now}
        if name is not None:
            set_data[CommunityModel.name] = name

        return await CommunityModel.find_one(CommunityModel.community_tid == community_tid).upsert(
            Set(set_data),
            on_insert=CommunityModel(community_tid=community_tid, name=name, first_saw=now, last_saw=now),
            response_type=UpdateResponse.NEW_DOCUMENT,
        )


class CommunityBanModel(Document):
    """A user banned from a whole community — mirror of ``FederationBan``.

    ``banned_chats`` records the community chats where the ban was actually applied.
    """

    community_tid: int
    user_id: int  # Telegram user ID of banned user (kept as int for performance)
    banned_chats: list[Link[ChatModel]] = Field(default_factory=list)
    time: datetime
    by: Link[ChatModel]  # User who performed the ban
    reason: str | None = None
    original_message_text: str | None = None

    class Settings:
        name = "community_bans"
        indexes: ClassVar = [
            IndexModel([("community_tid", ASCENDING), ("user_id", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("community_tid", ASCENDING)]),
        ]


class CommunityTask(Document):
    """Deferred community (un)ban task processed by ``ProcessCommunityBans``.

    Trimmed mirror of ``FederationTask``: the handler applies the ban to the DB
    record and the current chat synchronously, this queue propagates it across the
    rest of the community and edits the original reply with the final counts.
    """

    community_tid: int
    task_type: CommunityTaskType
    status: TaskStatus = TaskStatus.PENDING
    chat: Link[ChatModel]  # Chat where the command was issued
    user: Link[ChatModel]  # User who initiated the task (banner)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    target_user_id: int | None = None  # Telegram user ID of the (un)banned user
    current_chat_iid: PydanticObjectId | None = None  # Set when the issuing chat is part of the community
    reply_chat_id: int | None = None  # Chat/message of the reply to edit with the final result
    reply_message_id: int | None = None
    reason: str | None = None
    original_message_text: str | None = None
    silent: bool = False
    ban_id: PydanticObjectId | None = None  # BAN: the CommunityBanModel record to update
    unban_chat_iids: list[PydanticObjectId] = Field(default_factory=list)  # UNBAN: chats to clear
    banned_count: int = 0
    unbanned_count: int = 0

    class Settings:
        name = "community_tasks"
        indexes: ClassVar = [
            IndexModel([("community_tid", ASCENDING)]),
            IndexModel([("task_type", ASCENDING), ("status", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]
