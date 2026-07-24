from __future__ import annotations

from datetime import UTC, datetime

from beanie import Document
from pydantic import Field


class OpDebugSnapshotModel(Document):
    """Stored snapshot from /op_debug command invocations."""

    chat_id: int
    operator_id: int
    operator_name: str
    system_context: dict
    chat_context: dict
    redis_health: dict
    error_backoff: dict
    feature_flags: dict
    chat_history: list[dict]
    operator_notes: list[str]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "op_debug_snapshots"
