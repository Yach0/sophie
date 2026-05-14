from __future__ import annotations

from datetime import datetime, timezone

from beanie import Document
from pydantic import Field


class OpDebugFeatureRequestModel(Document):
    """Stored feature requests submitted via /op_debug with AI summarization."""

    chat_id: int
    operator_id: int
    operator_name: str
    title: str
    summary: str
    severity: str = "medium"
    key_points: list[str] = Field(default_factory=list)
    suggested_action: str = ""
    snapshot_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "op_debug_feature_requests"
