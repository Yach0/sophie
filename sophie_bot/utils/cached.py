from __future__ import annotations

import asyncio
import functools
import inspect
import math
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar, cast

import ujson
from redis.asyncio import Redis

from sophie_bot.utils.logger import log

T = TypeVar("T")
P = ParamSpec("P")
_NOT_SET_MARKER = "__sophie_not_set__"


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

    async def set_value(self, key: str, value: Any, ttl: float | None) -> None:
        expiry_timestamp = time.time() + ttl if ttl else None
        serialized = ujson.dumps({"v": value, "s": _NOT_SET_MARKER if value is None else None, "exp": expiry_timestamp})
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


def _should_early_recompute(expiry: float | None, beta: float) -> bool:
    if expiry is None or beta <= 0:
        return False
    time_until_expiry = expiry - time.time()
    if time_until_expiry <= 0:
        return True
    probability = beta * math.exp(-time_until_expiry / beta)
    return random.random() < probability


class CachedFunction[**P, T]:
    def __init__(
        self,
        cache: RedisCache,
        func: Callable[P, Awaitable[T]],
        ttl: float | None,
        key: str | None,
        no_self: bool,
        stampede_protection: bool,
        early_recompute_beta: float,
    ) -> None:
        self.cache = cache
        self.func = func
        self.signature = inspect.signature(func)
        self.ttl = ttl
        self.key = key
        self.no_self = no_self
        self.stampede_protection = stampede_protection
        self.early_recompute_beta = early_recompute_beta
        functools.update_wrapper(self, func)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        key = self._build_key(*args, **kwargs)
        cached_data = await self.cache.redis.get(key)
        if cached_data is not None:
            value, expiry, is_valid = _deserialize(cached_data)
            if is_valid:
                if self.early_recompute_beta > 0 and _should_early_recompute(expiry, self.early_recompute_beta):
                    log.debug("Cached: PER triggered early recomputation", key=key)
                    self.cache.spawn(self._recompute_and_store(key, *args, **kwargs))
                return cast(T, value)

        if self.stampede_protection:
            return await self._get_or_set_with_lock(key, *args, **kwargs)

        result = await self.func(*args, **kwargs)
        self.cache.spawn(self.cache.set_value(key, result, ttl=self.ttl))
        log.debug("Cached: writing new data", key=key)
        return result

    async def _get_or_set_with_lock(self, key: str, *args: P.args, **kwargs: P.kwargs) -> T:
        entry = self.cache._locks.acquire_entry(key)
        try:
            async with entry.lock:
                cached_data = await self.cache.redis.get(key)
                if cached_data is not None:
                    value, _expiry, is_valid = _deserialize(cached_data)
                    if is_valid:
                        return cast(T, value)
                result = await self.func(*args, **kwargs)
                await self.cache.set_value(key, result, ttl=self.ttl)
                log.debug("Cached: writing new data (lock holder)", key=key)
                return result
        finally:
            self.cache._locks.release_entry(key)

    async def _recompute_and_store(self, key: str, *args: P.args, **kwargs: P.kwargs) -> None:
        try:
            result = await self.func(*args, **kwargs)
            await self.cache.set_value(key, result, ttl=self.ttl)
            log.debug("Cached: PER background refresh complete", key=key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Cached: PER background refresh failed", key=key, error=str(exc))

    def _build_key(self, *args: Any, **kwargs: Any) -> str:
        func_module = getattr(self.func, "__module__", "") or ""
        func_name = getattr(self.func, "__name__", "unknown")
        base_key = self.key if self.key else f"{func_module}:{func_name}"
        bound = self.signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = list(bound.arguments.items())
        if self.no_self:
            arguments = arguments[1:]
        return base_key + "(" + ",".join(f"{name}={value!r}" for name, value in arguments) + ")"

    async def reset_cache(self, *args: Any, new_value: Any = None, **kwargs: Any) -> int | None:
        key = self._build_key(*args, **kwargs)
        if new_value is not None:
            await self.cache.set_value(key, new_value, ttl=self.ttl)
            return None
        return await self.cache.redis.delete(key)


class Cached:
    def __init__(
        self,
        cache: RedisCache,
        ttl: float | None = None,
        key: str | None = None,
        no_self: bool = False,
        stampede_protection: bool = True,
        early_recompute_beta: float = 1.0,
    ) -> None:
        self.cache = cache
        self.ttl = ttl
        self.key = key
        self.no_self = no_self
        self.stampede_protection = stampede_protection
        self.early_recompute_beta = early_recompute_beta

    def __call__(self, func: Callable[P, Awaitable[T]]) -> CachedFunction[P, T]:
        return CachedFunction(
            self.cache,
            func,
            ttl=self.ttl,
            key=self.key,
            no_self=self.no_self,
            stampede_protection=self.stampede_protection,
            early_recompute_beta=self.early_recompute_beta,
        )
