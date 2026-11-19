"""Preserve memory retention and migrate the combined capture/writer switch.

Snapshot existing rollout membership and enable the combined switch only where
both old switches were enabled. Retention moves to internal policy records.
Run with old bot and scheduler processes stopped. Rollback restores the original
writer settings and removes the copied retention policies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from beanie import free_fall_migration
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.services.migrations import MigrationResources
from sophie_bot.utils.feature_flags import (
    FeatureValue,
    _chat_rollout_bucket,
    _coerce_db_value,
    _coerce_rollout,
    get_rollout_percentage,
)

_WRITER = "ai_memory_writer"
_CAPTURE = "ai_memory_capture"
_RETENTION = "ai_memory_retention_days"
_STAGE = "__rollout__:"
_WRITER_FILTER = {"feature": {"$in": [_WRITER, _STAGE + _WRITER]}}
_BACKUP = "ai_memory_policy_migration_backup"
_POLICY = "ai_memory_retention_policy"


def _effective(
    rows: dict[tuple[str, int | None], Any],
    feature: str,
    chat_tid: int | None,
    now: datetime,
    default: FeatureValue,
) -> FeatureValue:
    for scope in (chat_tid, None):
        value = _coerce_db_value(rows.get((feature, scope)))
        if value is not None:
            return value
    rollout = _coerce_rollout(rows.get((_STAGE + feature, None)))
    if (
        chat_tid is not None
        and rollout
        and _chat_rollout_bucket(feature, chat_tid) < get_rollout_percentage(rollout, now)
    ):
        return rollout["value"]
    return default


def _days(value: FeatureValue) -> int:
    try:
        return max(1, min(365, int(value)))
    except (ValueError, OverflowError):
        return 30


async def _clear_writer_cache(resources: MigrationResources) -> None:
    redis = resources.redis
    async for key in redis.scan_iter(match="sophie:kill_switch_chat:*"):
        await redis.hdel(key, _WRITER)
    await redis.hdel("sophie:kill_switch", _WRITER)
    await redis.hdel("sophie:kill_switch_rollout", _WRITER)


class Forward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        db = resources.database.database
        overrides = db["feature_flag_overrides"]
        backup = db[_BACKUP]
        snapshot = await backup.find_one({"_id": "snapshot"}, session=session)
        if snapshot and snapshot.get("complete"):
            return
        if snapshot is None:
            async for original in overrides.find(_WRITER_FILTER, session=session):
                await backup.update_one(
                    {"_id": original["_id"]},
                    {"$setOnInsert": {"original": original}},
                    upsert=True,
                    session=session,
                )
            now = datetime.now(UTC)
            await backup.update_one({"_id": "snapshot"}, {"$setOnInsert": {"at": now}}, upsert=True, session=session)
        else:
            now = snapshot["at"].replace(tzinfo=UTC)

        rows = {
            (row["feature"], row.get("chat_tid")): row["value"]
            async for row in overrides.find(
                {"feature": {"$in": [_CAPTURE, _RETENTION, _STAGE + _CAPTURE, _STAGE + _RETENTION]}},
                session=session,
            )
        }
        original_writers = [
            row["original"] async for row in backup.find({"original": {"$exists": True}}, session=session)
        ]
        rows.update({(row["feature"], row.get("chat_tid")): row["value"] for row in original_writers})
        writer_global = bool(_effective(rows, _WRITER, None, now, False)) and bool(
            _effective(rows, _CAPTURE, None, now, False)
        )
        retention_global = _days(_effective(rows, _RETENTION, None, now, 30))
        policy = db[_POLICY]
        if retention_global != 30:
            await policy.update_one({"chat_tid": 0}, {"$set": {"days": retention_global}}, upsert=True, session=session)

        async def migrate_chat(chat_tid: int) -> None:
            writer = bool(_effective(rows, _WRITER, chat_tid, now, False)) and bool(
                _effective(rows, _CAPTURE, chat_tid, now, False)
            )
            if writer != writer_global or (_WRITER, chat_tid) in rows:
                await overrides.update_one(
                    {"feature": _WRITER, "chat_tid": chat_tid},
                    {"$set": {"value": writer, "source": "manual"}},
                    upsert=True,
                    session=session,
                )
            days = _days(_effective(rows, _RETENTION, chat_tid, now, 30))
            if days != retention_global:
                await policy.update_one({"chat_tid": chat_tid}, {"$set": {"days": days}}, upsert=True, session=session)

        explicit_chats = {chat_tid for _, chat_tid in rows if chat_tid is not None}
        for chat_tid in sorted(explicit_chats):
            await migrate_chat(chat_tid)
        if any((_STAGE + feature, None) in rows for feature in (_WRITER, _CAPTURE, _RETENTION)):
            async for chat in db["chats"].find({}, {"chat_id": 1}, session=session):
                chat_tid = chat["chat_id"]
                if chat_tid not in explicit_chats:
                    await migrate_chat(chat_tid)

        await overrides.delete_one({"feature": _STAGE + _WRITER, "chat_tid": None}, session=session)
        if (_WRITER, None) in rows:
            await overrides.update_one(
                {"feature": _WRITER, "chat_tid": None}, {"$set": {"value": writer_global}}, session=session
            )
        await _clear_writer_cache(resources)
        await backup.update_one({"_id": "snapshot"}, {"$set": {"complete": True}}, session=session)


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        db = resources.database.database
        backup = db[_BACKUP]
        if await backup.find_one({"_id": "snapshot"}, session=session) is None:
            return
        overrides = db["feature_flag_overrides"]
        await overrides.delete_many(_WRITER_FILTER, session=session)
        async for row in backup.find({"original": {"$exists": True}}, session=session):
            await overrides.insert_one(row["original"], session=session)
        await db[_POLICY].delete_many({}, session=session)
        await _clear_writer_cache(resources)
        await backup.delete_many({}, session=session)
