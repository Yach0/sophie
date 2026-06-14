from __future__ import annotations

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import count_metric, distribution_metric


def track_captcha_sent(*, chat_type: str = "group") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.welcome.captcha.sent", attributes={"chat_type": chat_type})


def track_captcha_passed(*, chat_type: str = "group") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.welcome.captcha.passed", attributes={"chat_type": chat_type})


def track_captcha_failed(reason: str, *, chat_type: str = "group") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.welcome.captcha.failed", attributes={"reason": reason, "chat_type": chat_type})


def track_captcha_duration(seconds: float, *, chat_type: str = "group") -> None:
    if not CONFIG.metrics_enable:
        return

    distribution_metric(
        "sophie.welcome.captcha.duration",
        seconds,
        attributes={"chat_type": chat_type},
        unit="second",
    )
