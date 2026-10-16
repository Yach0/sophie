from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import ujson
from redis.asyncio import Redis

from sophie_bot.utils.logger import log


class _LockEntry:
    __slots__ = ("lock", "waiters")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.waiters = 0


class _LockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, _LockEntry] = {}

    def acquire_entry(self, key: str) -> _LockEntry:
        entry = self._locks.get(key)
        if entry is None:
            entry = _LockEntry()
            self._locks[key] = entry
        entry.waiters += 1
        return entry

    def release_entry(self, key: str) -> None:
        entry = self._locks.get(key)
        if entry is None:
            return
        entry.waiters -= 1
        if entry.waiters <= 0 and not entry.lock.locked():
            self._locks.pop(key, None)


class RedisCache:
    """Own cache-local locks and background refresh/write tasks."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._locks = _LockRegistry()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, awaitable: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(awaitable)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @asynccontextmanager
    async def lock(self, key: str) -> AsyncIterator[None]:
        entry = self._locks.acquire_entry(key)
        try:
            async with entry.lock:
                yield
        finally:
            self._locks.release_entry(key)

    async def set_value(self, key: str, value: Any, ttl: float | None) -> None:
        expiry_timestamp = time.time() + ttl if ttl else None
        serialized = ujson.dumps({"v": value, "exp": expiry_timestamp})
        await self.redis.set(key, serialized)
        if ttl:
            await self.redis.expire(key, int(ttl))

    async def close(self) -> None:
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()


def _deserialize(data: bytes | str) -> tuple[Any, float | None, bool]:
    try:
        parsed = ujson.loads(data)
        if isinstance(parsed, dict) and "v" in parsed:
            return parsed["v"], parsed.get("exp"), True
    except (ujson.JSONDecodeError, TypeError):
        pass
    return None, None, False


def _should_early_recompute(expiry: float | None) -> bool:
    if expiry is None:
        return False
    time_until_expiry = expiry - time.time()
    if time_until_expiry <= 0:
        return True
    probability = math.exp(-time_until_expiry)
    return random.random() < probability


class Cached[CacheArgument, CacheValue]:
    """Cache a single-argument async lookup under an explicitly supplied Redis key."""

    def __init__(
        self,
        cache: RedisCache,
        load: Callable[[CacheArgument], Awaitable[CacheValue]],
        *,
        ttl: float | None,
        key: Callable[[CacheArgument], str],
    ) -> None:
        self.cache = cache
        self.load = load
        self.ttl = ttl
        self.key = key

    async def __call__(self, argument: CacheArgument) -> CacheValue:
        key = self.key(argument)
        cached_data = await self.cache.redis.get(key)
        if cached_data is not None:
            value, expiry, is_valid = _deserialize(cached_data)
            if is_valid:
                if _should_early_recompute(expiry):
                    log.debug("Cached: PER triggered early recomputation", key=key)
                    self.cache.spawn(self._recompute_and_store(key, argument))
                return cast(CacheValue, value)

        return await self._get_or_set_with_lock(key, argument)

    async def _get_or_set_with_lock(self, key: str, argument: CacheArgument) -> CacheValue:
        async with self.cache.lock(key):
            cached_data = await self.cache.redis.get(key)
            if cached_data is not None:
                value, _expiry, is_valid = _deserialize(cached_data)
                if is_valid:
                    return cast(CacheValue, value)
            result = await self.load(argument)
            await self.cache.set_value(key, result, ttl=self.ttl)
            log.debug("Cached: writing new data (lock holder)", key=key)
            return result

    async def _recompute_and_store(self, key: str, argument: CacheArgument) -> None:
        try:
            result = await self.load(argument)
            await self.cache.set_value(key, result, ttl=self.ttl)
            log.debug("Cached: PER background refresh complete", key=key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Cached: PER background refresh failed", key=key, error=str(exc))

    async def reset_cache(self, argument: CacheArgument) -> int:
        return await self.cache.redis.delete(self.key(argument))
