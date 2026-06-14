from __future__ import annotations

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import count_metric


def track_note_saved(*, has_media: bool, chat_type: str = "unknown") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric(
        "sophie.notes.saved",
        attributes={"has_media": has_media, "chat_type": chat_type},
    )


def track_note_retrieved(*, trigger: str, has_media: bool, chat_type: str = "unknown") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric(
        "sophie.notes.retrieved",
        attributes={"trigger": trigger, "has_media": has_media, "chat_type": chat_type},
    )


def track_note_deleted(*, count: int = 1, chat_type: str = "unknown") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.notes.deleted", count, attributes={"chat_type": chat_type})
