"""Shared state for communicating restrictive filter triggers across routers.

Maps message IDs to a flag indicating whether a restrictive filter action
(mute/ban/kick/delete message) was triggered. Entries are cleaned up
after a TTL to prevent memory leaks.
"""

from __future__ import annotations

import time

# TTL in seconds for entries in the state dict
_TTL_SECONDS = 60

# Maps message_id -> timestamp when it was marked
_restrictive_messages: dict[int, float] = {}


def mark_restrictive(message_id: int) -> None:
    """Mark a message as having triggered a restrictive filter action."""
    _restrictive_messages[message_id] = time.monotonic()
    _cleanup()


def was_restrictive_triggered(message_id: int) -> bool:
    """Check whether a restrictive filter was triggered for a given message ID."""
    triggered = message_id in _restrictive_messages
    if triggered:
        _restrictive_messages.pop(message_id, None)
        _cleanup()
    return triggered


def _cleanup() -> None:
    """Remove expired entries from the state dict."""
    now = time.monotonic()
    expired = [msg_id for msg_id, ts in _restrictive_messages.items() if now - ts > _TTL_SECONDS]
    for msg_id in expired:
        _restrictive_messages.pop(msg_id, None)
