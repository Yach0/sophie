from __future__ import annotations

from enum import Enum


class CommunityTaskType(str, Enum):
    """Type of a deferred community task processed by the scheduler."""

    BAN = "ban"
    UNBAN = "unban"
