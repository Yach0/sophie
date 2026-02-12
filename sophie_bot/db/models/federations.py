from datetime import datetime
from typing import Optional

from beanie import Document, BeanieObjectId
from pymongo import ASCENDING, IndexModel
from ._link_type import Link
from pydantic import Field

from sophie_bot.db.models.federations_enums import TaskStatus
from sophie_bot.db.models.chat import ChatModel


class Federation(Document):
    """Federation model - matches existing DB schema exactly"""

    fed_name: str
    fed_id: str
    creator: Link[ChatModel]
    chats: list[Link[ChatModel]] = Field(default_factory=list)
    subscribed: list[str] = Field(default_factory=list)
    admins: list[Link[ChatModel]] = Field(default_factory=list)
    log_chat: Optional[Link[ChatModel]] = None

    class Settings:
        name = "feds"
        indexes = [
            IndexModel([("fed_id", ASCENDING)]),
            IndexModel([("creator.$id", ASCENDING)]),
            IndexModel([("chats.$id", ASCENDING)]),
            IndexModel([("creator.$id", ASCENDING), ("fed_name", ASCENDING)]),
        ]


class FederationBan(Document):
    """Federation ban model - uses user_id for user, Link for banned_chats and by."""

    fed_id: str
    user_id: int  # Telegram user ID of banned user (kept as int for performance)
    banned_chats: list[Link[ChatModel]] = Field(default_factory=list)  # Chats where user was banned
    time: datetime
    by: Link[ChatModel]  # User who performed the ban
    reason: Optional[str] = None
    origin_fed: Optional[str] = None  # For subscribed federation bans
    fimport_id: Optional[BeanieObjectId] = None

    class Settings:
        name = "fed_bans"
        indexes = [
            IndexModel([("fed_id", ASCENDING), ("user_id", ASCENDING)]),
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("fed_id", ASCENDING)]),
            IndexModel([("by.$id", ASCENDING)]),
        ]


class FederationImportTask(Document):
    """Federation import task model for CSV ban list imports"""

    fed_id: str
    chat: Link[ChatModel]  # Chat where the import command was issued
    user: Link[ChatModel]  # User who initiated the import
    file_id: str  # Telegram file ID for the uploaded CSV
    status: TaskStatus = TaskStatus.PENDING
    error_message: Optional[str] = None
    imported_count: int = 0
    failed_count: int = 0
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Settings:
        name = "fed_import_tasks"
        indexes = [
            IndexModel([("fed_id", ASCENDING)]),
            IndexModel([("user.$id", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]


class FederationExportTask(Document):
    """Task for exporting federation ban lists to CSV files"""

    fed_id: str
    fed_name: str
    chat: Link[ChatModel]
    user: Link[ChatModel]
    file_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    ban_count: int = 0
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    file_size_bytes: Optional[int] = None

    class Settings:
        name = "fed_export_tasks"
        indexes = [
            IndexModel([("fed_id", ASCENDING)]),
            IndexModel([("user.$id", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]
