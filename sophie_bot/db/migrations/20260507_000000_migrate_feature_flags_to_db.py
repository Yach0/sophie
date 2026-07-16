"""Migration: migrate_feature_flags_to_db

Description:
    Copies feature flag overrides from Redis hashes into MongoDB so Redis can
    become a cache instead of the source of truth.

Affected Collections:
    - feature_flag_overrides

Impact:
    - Low risk: Redis data is preserved and remains usable as warm cache.

Rollback:
    Restores every override to Redis and then removes exactly the rows it restored, so no
    override is dropped without first being written back.

    Backward previously skipped any override whose feature is no longer in FEATURE_FLAGS, or
    whose value is None, and then unconditionally dropped the whole collection -- destroying
    precisely the rows it had declined to restore. Overrides for a retired or renamed flag
    were therefore lost on rollback. Rows that cannot be serialized back into Redis are now
    left in place rather than deleted.
"""

from __future__ import annotations

from beanie import free_fall_migration
from bson import ObjectId

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
        raw_global_overrides = await aredis.hgetall(_REDIS_KEY)

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

            raw_chat_overrides = await aredis.hgetall(redis_key)
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
    """Restore feature flag overrides to Redis and remove the rows that were restored."""

    @free_fall_migration(document_models=[FeatureFlagOverride])
    async def rollback(self, session: object) -> None:
        collection = FeatureFlagOverride.get_pymongo_collection()
        restored_ids: list[ObjectId] = []

        # Deliberately not filtered on FEATURE_FLAGS: an override for a retired flag is still
        # the operator's data, and Redis stores it as an inert hash field either way.
        async for override in collection.find({}, session=session):
            value = override.get("value")
            if value is None:
                continue

            chat_tid = override.get("chat_tid")
            redis_key = _REDIS_KEY if chat_tid is None else f"{_REDIS_CHAT_KEY_PREFIX}:{chat_tid}"
            await aredis.hset(redis_key, override["feature"], _serialize_value(value))
            restored_ids.append(override["_id"])

        await collection.delete_many({"_id": {"$in": restored_ids}}, session=session)
