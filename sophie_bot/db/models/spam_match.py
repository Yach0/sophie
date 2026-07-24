from __future__ import annotations

from datetime import UTC, datetime

from beanie import Document
from pydantic import Field


class SpamMatchModel(Document):
    text: str
    spam_probability: float
    nsfw_probability: float
    chat_tid: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "spam_matches"
        use_revision = False
