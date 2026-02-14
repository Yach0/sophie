from datetime import datetime, timezone

from beanie import Document
from pydantic import Field

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel


class ChatLeaveLogModel(Document):
    """Model for logging when Sophie is forced to leave a chat due to permission issues."""

    chat: Link[ChatModel]
    reason: str
    error_message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "chat_leave_logs"
