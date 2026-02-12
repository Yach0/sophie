from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    """Status values for federation import/export tasks."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
