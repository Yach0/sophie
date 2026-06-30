from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    """Status values for federation import/export tasks."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FederationTaskType(str, Enum):
    """Type of a deferred federation task processed by the scheduler."""

    IMPORT = "import"
    EXPORT = "export"
    BAN = "ban"
    UNBAN = "unban"
