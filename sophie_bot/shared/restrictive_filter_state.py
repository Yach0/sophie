from __future__ import annotations

import time

_restrictive_triggered: dict[tuple[int, int], float] = {}
_TTL_SECONDS: float = 300  # 5 minutes


def mark_restrictive_triggered(chat_id: int, message_id: int) -> None:
    _restrictive_triggered[(chat_id, message_id)] = time.monotonic()
    _evict_expired()


def is_restrictive_triggered(chat_id: int, message_id: int) -> bool:
    _evict_expired()
    return _restrictive_triggered.pop((chat_id, message_id), 0) > 0


def _evict_expired() -> None:
    now = time.monotonic()
    expired = [key for key, ts in _restrictive_triggered.items() if now - ts > _TTL_SECONDS]
    for key in expired:
        del _restrictive_triggered[key]
