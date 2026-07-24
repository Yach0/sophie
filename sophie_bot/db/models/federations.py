from datetime import datetime
from typing import ClassVar

from beanie import BeanieObjectId, Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations_enums import FederationTaskType, TaskStatus

from ._link_type import Link


class Federation(Document):
    """Federation model - matches existing DB schema exactly"""

    fed_name: str
    fed_id: str
    creator: Link[ChatModel]
    chats: list[Link[ChatModel]] = Field(default_factory=list)
    subscribed: list[str] = Field(default_factory=list)
    admins: list[Link[ChatModel]] = Field(default_factory=list)
    log_chat: Link[ChatModel] | None = None

    class Settings:
        name = "feds"
        indexes: ClassVar = [
            IndexModel([("fed_id", ASCENDING)]),
            IndexModel([("creator.$id", ASCENDING)]),
            IndexModel([("chats.$id", ASCENDING)]),
            IndexModel([("admins.$id", ASCENDING)]),
            IndexModel([("creator.$id", ASCENDING), ("fed_name", ASCENDING)]),
        ]


class FederationBan(Document):
    """Federation ban model - uses user_id for user, Link for banned_chats and by."""

    fed_id: str
    user_id: int  # Telegram user ID of banned user (kept as int for performance)
    banned_chats: list[Link[ChatModel]] = Field(default_factory=list)  # Chats where user was banned
    time: datetime
    by: Link[ChatModel]  # User who performed the ban
    reason: str | None = None
    original_message_text: str | None = None
    origin_fed: str | None = None  # For subscribed federation bans
    fimport_id: BeanieObjectId | None = None

    class Settings:
        name = "fed_bans"
        indexes: ClassVar = [
            IndexModel([("fed_id", ASCENDING), ("user_id", ASCENDING)]),
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("fed_id", ASCENDING)]),
            IndexModel([("by.$id", ASCENDING)]),
        ]


class FederationTask(Document):
    """Unified deferred federation task processed by the scheduler.

    A single collection backs all federation background work — CSV imports/exports
    and ban/unban propagation — discriminated by ``task_type``. Type-specific fields
    are optional and only populated for the relevant task type.
    """

    fed_id: str
    task_type: FederationTaskType
    status: TaskStatus = TaskStatus.PENDING
    chat: Link[ChatModel]  # Chat where the command was issued
    user: Link[ChatModel]  # User who initiated the task (importer / exporter / banner)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # IMPORT / EXPORT
    file_id: str | None = None  # Telegram file ID (uploaded CSV for import, generated CSV for export)
    fed_name: str | None = None  # EXPORT: snapshot of the federation name for the caption
    imported_count: int = 0
    failed_count: int = 0
    ban_count: int = 0
    file_size_bytes: int | None = None

    # BAN / UNBAN
    target_user_id: int | None = None  # Telegram user ID of the (un)banned user
    current_chat_iid: BeanieObjectId | None = None  # Set when the issuing chat is part of the fed
    reply_chat_id: int | None = None  # Chat/message of the reply to edit with the final result
    reply_message_id: int | None = None
    reason: str | None = None
    original_message_text: str | None = None
    silent: bool = False
    banner_anonymous: bool = False  # BAN: hide the banner in the public reply (anonymous admin)
    ban_id: BeanieObjectId | None = None  # BAN: the FederationBan record to update
    unban_chat_iids: list[BeanieObjectId] = Field(default_factory=list)  # UNBAN: chats to clear
    banned_count: int = 0
    lazy_ban_count: int = 0
    unbanned_count: int = 0

    class Settings:
        name = "fed_tasks"
        indexes: ClassVar = [
            IndexModel([("fed_id", ASCENDING)]),
            IndexModel([("task_type", ASCENDING), ("status", ASCENDING)]),
            IndexModel([("user.$id", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]


# Legacy task collections, superseded by the unified ``FederationTask`` (``fed_tasks``).
# These are NOT registered as active models; they exist only so the historical
# ``convert_federations_to_links`` migration can still address the original collections.
class FederationImportTask(Document):
    class Settings:
        name = "fed_import_tasks"


class FederationExportTask(Document):
    class Settings:
        name = "fed_export_tasks"
