"""Deprecated duration conversion helpers.

This module is kept only for legacy greeting duration migrations and should not
be used by new runtime code.
"""

import re
from datetime import timedelta

TIMEDELTA_PATTERN = re.compile(
    r"^(?:(?P<days>\d+)\s+days?,\s+)?(?P<hours>\d{1,2}):(?P<minutes>\d{2}):(?P<seconds>\d{2})$"
)


def convert_timedelta_or_str(value: str | timedelta) -> timedelta:
    if isinstance(value, timedelta):
        return value

    # Simple implementation to handle basics if original is lost
    # Format usually like "1d", "1h", "10m"
    if not isinstance(value, str):
        raise TypeError(f"Cannot convert {type(value)} to timedelta")

    value = value.lower()
    if match := TIMEDELTA_PATTERN.match(value):
        return timedelta(
            days=int(match.group("days") or 0),
            hours=int(match.group("hours")),
            minutes=int(match.group("minutes")),
            seconds=int(match.group("seconds")),
        )
    if value.endswith("d"):
        return timedelta(days=int(value[:-1]))
    if value.endswith("h"):
        return timedelta(hours=int(value[:-1]))
    if value.endswith("m"):
        return timedelta(minutes=int(value[:-1]))
    if value.endswith("s"):
        return timedelta(seconds=int(value[:-1]))
    if value.endswith("w"):
        return timedelta(weeks=int(value[:-1]))

    return timedelta(seconds=0)
