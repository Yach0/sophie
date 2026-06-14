from __future__ import annotations

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import count_metric, distribution_metric


def track_federation_ban(*, chats_banned: int = 0) -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.federation.ban.applied")
    if chats_banned:
        distribution_metric("sophie.federation.ban.chats_affected", chats_banned)


def track_federation_import_completed(*, items_imported: int, items_failed: int) -> None:
    if not CONFIG.metrics_enable:
        return

    distribution_metric(
        "sophie.federation.import.items",
        items_imported,
        attributes={"status": "imported"},
    )
    if items_failed:
        distribution_metric(
            "sophie.federation.import.items",
            items_failed,
            attributes={"status": "failed"},
        )
