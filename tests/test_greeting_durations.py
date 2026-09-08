from __future__ import annotations

from datetime import timedelta

from sophie_bot.db.models.greetings import WelcomeMute, WelcomeSecurity
from sophie_bot.modules.welcomesecurity.utils_.db_time_convert import convert_timedelta_or_str


def test_convert_timedelta_or_str_accepts_compact_duration() -> None:
    assert convert_timedelta_or_str("48h") == timedelta(hours=48)
    assert convert_timedelta_or_str("2d") == timedelta(days=2)


def test_convert_timedelta_or_str_accepts_python_timedelta_string() -> None:
    assert convert_timedelta_or_str("2 days, 0:00:00") == timedelta(days=2)
    assert convert_timedelta_or_str("0:30:05") == timedelta(minutes=30, seconds=5)


def test_greeting_duration_models_use_timedelta_defaults() -> None:
    assert WelcomeMute().time == timedelta(hours=48)
    assert WelcomeSecurity().expire == timedelta(hours=48)


def test_welcome_security_preserves_legacy_numeric_durations() -> None:
    assert WelcomeSecurity(expire=172_800_000).expire == timedelta(hours=48)
    assert WelcomeSecurity(expire=21_600.0).expire == timedelta(hours=6)
