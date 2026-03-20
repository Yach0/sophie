from __future__ import annotations

import json
from typing import Any
from sophie_bot.db.models import LocksModel
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

CACHE_KEY_PREFIX = "locks:"
CACHE_TTL = 300


async def get_cached_locks(chat_tid: int, chat_iid: Any) -> set[str] | None:
    key = f"{CACHE_KEY_PREFIX}{chat_tid}"
    try:
        data = await aredis.get(key)
        if data:
            return set(json.loads(data))
    except Exception as e:
        log.debug("Error getting cached locks", error=str(e))
    try:
        model = await LocksModel.find_one(LocksModel.chat.id == chat_iid)
        if not model:
            model = LocksModel(chat=chat_iid)
            return set()
        locked_types = model.locked_types
        await set_cached_locks(chat_tid, locked_types)
        return locked_types
    except Exception as e:
        log.debug("Error fetching locks from database", error=str(e))
        return set()


async def set_cached_locks(chat_tid: int, locks: set[str]) -> None:
    key = f"{CACHE_KEY_PREFIX}{chat_tid}"
    try:
        await aredis.set(key, json.dumps(list(locks)), ex=CACHE_TTL)
    except Exception as e:
        log.debug("Error setting cached locks", error=str(e))


async def invalidate_locks_cache(chat_tid: int) -> None:
    key = f"{CACHE_KEY_PREFIX}{chat_tid}"
    try:
        await aredis.delete(key)
    except Exception as e:
        log.debug("Error invalidating locks cache", error=str(e))
