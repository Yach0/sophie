from __future__ import annotations

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import count_metric, distribution_metric


def track_moderation_action(
    action: str,
    *,
    chat_type: str = "unknown",
    is_temporary: bool = False,
) -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric(
        "sophie.moderation.action",
        attributes={"action": action, "chat_type": chat_type, "is_temporary": is_temporary},
    )


def track_warn_threshold_reached(auto_action: str, *, chat_type: str = "unknown") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric(
        "sophie.moderation.warn.threshold",
        attributes={"auto_action": auto_action, "chat_type": chat_type},
    )


def track_purge(message_count: int) -> None:
    if not CONFIG.metrics_enable:
        return

    distribution_metric("sophie.moderation.purge.messages", message_count)
