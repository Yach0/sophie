"""Migration: migrate_feature_flags_to_db

Description:
    Copies feature flag overrides from Redis hashes into MongoDB so Redis can
    become a cache instead of the source of truth.

Affected Collections:
    - feature_flag_overrides

Impact:
    - Low risk: Redis data is preserved and remains usable as warm cache.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import cast

from beanie import free_fall_migration

from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import FEATURE_FLAGS, _parse_override, _serialize_value

_REDIS_KEY = "sophie:kill_switch"
_REDIS_CHAT_KEY_PREFIX = "sophie:kill_switch_chat"


def _decode_redis_value(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _parse_chat_tid(redis_key: str) -> int | None:
    prefix = f"{_REDIS_CHAT_KEY_PREFIX}:"
    if not redis_key.startswith(prefix):
        return None
    try:
        return int(redis_key.removeprefix(prefix))
    except ValueError:
        return None


class Forward:
    """Copy global and per-chat feature flag overrides from Redis to MongoDB."""

    @free_fall_migration(document_models=[FeatureFlagOverride])
    async def migrate(self, session: object) -> None:
        collection = FeatureFlagOverride.get_pymongo_collection()
        raw_global_overrides = await cast(Awaitable[dict[bytes | str, bytes | str]], aredis.hgetall(_REDIS_KEY))

        for raw_feature, raw_value in raw_global_overrides.items():
            feature = _decode_redis_value(raw_feature)
            if feature not in FEATURE_FLAGS:
                continue
            value = _parse_override(raw_value, "")
            if value is None:
                continue
            await collection.update_one(
                {"feature": feature, "chat_tid": None},
                {"$set": {"feature": feature, "chat_tid": None, "value": value}},
                upsert=True,
                session=session,
            )

        async for raw_key in aredis.scan_iter(f"{_REDIS_CHAT_KEY_PREFIX}:*"):
            redis_key = _decode_redis_value(raw_key)
            chat_tid = _parse_chat_tid(redis_key)
            if chat_tid is None:
                continue

            raw_chat_overrides = await cast(Awaitable[dict[bytes | str, bytes | str]], aredis.hgetall(redis_key))
            for raw_feature, raw_value in raw_chat_overrides.items():
                feature = _decode_redis_value(raw_feature)
                if feature not in FEATURE_FLAGS:
                    continue
                value = _parse_override(raw_value, "")
                if value is None:
                    continue
                await collection.update_one(
                    {"feature": feature, "chat_tid": chat_tid},
                    {"$set": {"feature": feature, "chat_tid": chat_tid, "value": value}},
                    upsert=True,
                    session=session,
                )


class Backward:
    """Restore feature flag overrides to Redis and remove the MongoDB collection."""

    @free_fall_migration(document_models=[FeatureFlagOverride])
    async def rollback(self, session: object) -> None:
        collection = FeatureFlagOverride.get_pymongo_collection()

        async for override in collection.find({}, session=session):
            feature = override.get("feature")
            value = override.get("value")
            chat_tid = override.get("chat_tid")
            if feature not in FEATURE_FLAGS or value is None:
                continue

            redis_key = _REDIS_KEY if chat_tid is None else f"{_REDIS_CHAT_KEY_PREFIX}:{chat_tid}"
            await cast(Awaitable[int], aredis.hset(redis_key, feature, _serialize_value(value)))

        await collection.drop(session=session)
