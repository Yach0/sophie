"""Migration: convert_greeting_durations

Description:
    Converts greeting mute/security duration fields from string values such as
    `48h` and `2 days, 0:00:00` to MongoDB timedelta-compatible millisecond
    integers.

Affected Collections:
    - greetings

Impact:
    - Keeps semantic duration values unchanged.
    - Backward migration restores compact string values for compatibility.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.greetings import GreetingsModel
from sophie_bot.modules.welcomesecurity.utils_.db_time_convert import convert_timedelta_or_str


def _duration_to_milliseconds(value: str | timedelta | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return int(convert_timedelta_or_str(value).total_seconds() * 1000)


def _milliseconds_to_compact_duration(value: int | None) -> str | None:
    if value is None:
        return None

    seconds = value // 1000
    if seconds % 604800 == 0:
        return f"{seconds // 604800}w"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


class Forward:
    """Convert greeting duration fields to typed timedelta storage."""

    @free_fall_migration(document_models=[GreetingsModel])
    async def convert(self, session) -> None:
        collection = GreetingsModel.get_pymongo_collection()

        async for document in collection.find(session=session):
            updates: dict[str, Any] = {}
            welcome_mute = document.get("welcome_mute") or {}
            welcome_security = document.get("welcome_security") or {}

            if "time" in welcome_mute:
                updates["welcome_mute.time"] = _duration_to_milliseconds(welcome_mute.get("time"))
            if "expire" in welcome_security:
                updates["welcome_security.expire"] = _duration_to_milliseconds(welcome_security.get("expire"))

            if updates:
                await collection.update_one({"_id": document["_id"]}, {"$set": updates}, session=session)


class Backward:
    """Convert greeting duration fields back to compact strings."""

    @free_fall_migration(document_models=[GreetingsModel])
    async def convert(self, session) -> None:
        collection = GreetingsModel.get_pymongo_collection()

        async for document in collection.find(session=session):
            updates: dict[str, Any] = {}
            welcome_mute = document.get("welcome_mute") or {}
            welcome_security = document.get("welcome_security") or {}

            if isinstance(welcome_mute.get("time"), int):
                updates["welcome_mute.time"] = _milliseconds_to_compact_duration(welcome_mute.get("time"))
            if isinstance(welcome_security.get("expire"), int):
                updates["welcome_security.expire"] = _milliseconds_to_compact_duration(welcome_security.get("expire"))

            if updates:
                await collection.update_one({"_id": document["_id"]}, {"$set": updates}, session=session)
