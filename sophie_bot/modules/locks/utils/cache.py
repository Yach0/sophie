from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from sophie_bot.db.models import LocksModel
from sophie_bot.utils.logger import log

CACHE_KEY_PREFIX = "locks:"
CACHE_TTL = 300


async def get_cached_locks(
    chat_tid: int,
    chat_iid: Any,
    *,
    redis: Redis,
) -> set[str] | None:
    key = f"{CACHE_KEY_PREFIX}{chat_tid}"
    try:
        data = await redis.get(key)
        if data:
            return set(json.loads(data))
    except RedisError as error:
        log.debug("Error getting cached locks", error=str(error))
    try:
        model = await LocksModel.find_one(LocksModel.chat.id == chat_iid)
        if not model:
            return set()
        locked_types = model.locked_types
        await set_cached_locks(chat_tid, locked_types, redis=redis)
        return locked_types
    except Exception as e:  # noqa: BLE001  # DB fetch failure degrades to empty lock set
        log.debug("Error fetching locks from database", error=str(e))
        return set()


async def set_cached_locks(chat_tid: int, locks: set[str], *, redis: Redis) -> None:
    key = f"{CACHE_KEY_PREFIX}{chat_tid}"
    try:
        await redis.set(key, json.dumps(list(locks)), ex=CACHE_TTL)
    except RedisError as error:
        log.debug("Error setting cached locks", error=str(error))


async def invalidate_locks_cache(chat_tid: int, *, redis: Redis) -> None:
    key = f"{CACHE_KEY_PREFIX}{chat_tid}"
    try:
        await redis.delete(key)
    except RedisError as error:
        log.debug("Error invalidating locks cache", error=str(error))
