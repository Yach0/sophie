# Copyright (C) 2018 - 2020 MrYacha. All rights reserved. Source code available under the AGPL.
#
# This file is part of SophieBot.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
import functools
import math
import random
import time
from typing import Any, Awaitable, Callable, TypeVar, Union

import ujson

from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

T = TypeVar("T")

# Sentinel value to distinguish cached None from cache miss
_NOT_SET_MARKER = "__sophie_not_set__"

# Interval (in seconds) between lock registry cleanup sweeps
_LOCK_CLEANUP_INTERVAL = 300


async def set_value(key: str, value: Any, ttl: int | float | None) -> None:
    """Serialize and store a value in Redis with optional TTL."""
    expiry_timestamp = time.time() + ttl if ttl else None
    wrapped = {
        "v": value,
        "s": _NOT_SET_MARKER if value is None else None,
        "exp": expiry_timestamp,
    }
    serialized = ujson.dumps(wrapped)
    await aredis.set(key, serialized)
    if ttl:
        await aredis.expire(key, int(ttl))


def _deserialize(data: bytes | str) -> tuple[Any, float | None, bool]:
    """Deserialize cached data.

    Returns:
        A tuple of (value, expiry_timestamp_or_none, is_valid).
    """
    try:
        parsed = ujson.loads(data)
        if isinstance(parsed, dict) and "v" in parsed:
            expiry = parsed.get("exp")
            return parsed["v"], expiry, True
        # Legacy format or invalid - treat as cache miss
        return None, None, False
    except (ujson.JSONDecodeError, TypeError):
        return None, None, False


def _should_early_recompute(expiry: float | None, beta: float) -> bool:
    """Determine whether to trigger probabilistic early recomputation.

    Uses the PER (Probabilistic Early Recomputation) algorithm:
    probability increases as we approach the TTL expiry.

    Args:
        expiry: Unix timestamp when the cache entry expires, or None.
        beta: Controls aggressiveness of early recomputation. Higher values
              mean earlier recomputation on average. 0 disables PER.

    Returns:
        True if early recomputation should be triggered.
    """
    if expiry is None or beta <= 0:
        return False

    time_until_expiry = expiry - time.time()
    if time_until_expiry <= 0:
        # Already expired, treat as a miss
        return True

    # Probability grows as we approach expiry.
    # Using: P = random() < beta * ln(1 + (1 / time_until_expiry_ratio))
    # where time_until_expiry_ratio = time_until_expiry / total_ttl
    # Simplified: as time_until_expiry shrinks, probability increases.
    # We use a form that doesn't require knowing the original TTL:
    # P = random() < beta * ln(1 + 1/time_until_expiry)
    # But to keep it bounded, we use: beta * ln(1 + e^(-time_until_expiry / beta))
    # which approximates 0 when time_until_expiry >> beta and ~1 when near 0.
    probability = beta * math.exp(-time_until_expiry / beta)
    return random.random() < probability


class _LockEntry:
    """A lock entry that tracks how many waiters reference it."""

    __slots__ = ("lock", "waiters")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.waiters: int = 0


class _LockRegistry:
    """Registry of per-key asyncio locks with automatic cleanup.

    Locks are removed from the registry when no coroutines are waiting on them.
    A periodic sweep also removes any stale entries to guard against leaks.
    """

    def __init__(self) -> None:
        self._locks: dict[str, _LockEntry] = {}
        self._cleanup_task: asyncio.Task[None] | None = None

    def _ensure_cleanup_task(self) -> None:
        """Start the periodic cleanup task if not already running."""
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._cleanup_task = loop.create_task(self._periodic_cleanup())
            except RuntimeError:
                # No running loop yet; cleanup will start on first use in async context
                pass

    async def _periodic_cleanup(self) -> None:
        """Periodically remove lock entries with no waiters."""
        while True:
            await asyncio.sleep(_LOCK_CLEANUP_INTERVAL)
            stale_keys = [key for key, entry in self._locks.items() if entry.waiters <= 0 and not entry.lock.locked()]
            for key in stale_keys:
                self._locks.pop(key, None)
            if stale_keys:
                log.debug("Lock registry cleanup: removed stale entries", count=len(stale_keys))

    def acquire_entry(self, key: str) -> _LockEntry:
        """Get or create a lock entry for the given key, incrementing the waiter count."""
        self._ensure_cleanup_task()
        entry = self._locks.get(key)
        if entry is None:
            entry = _LockEntry()
            self._locks[key] = entry
        entry.waiters += 1
        return entry

    def release_entry(self, key: str) -> None:
        """Decrement the waiter count and remove the entry if no one else needs it."""
        entry = self._locks.get(key)
        if entry is None:
            return
        entry.waiters -= 1
        if entry.waiters <= 0 and not entry.lock.locked():
            self._locks.pop(key, None)


# Global lock registry shared across all cached instances
_lock_registry = _LockRegistry()


class cached:
    """Async caching decorator using Redis with JSON serialization.

    Supports cache stampede protection via per-key async locks and
    probabilistic early recomputation (PER).

    Usage:
        @cached(ttl=300)
        async def get_user(user_id: int) -> dict:
            return await fetch_user(user_id)

        # With stampede protection disabled
        @cached(ttl=60, stampede_protection=False)
        async def get_config(name: str) -> dict:
            return await load_config(name)

        # Reset cache for specific args
        await get_user.reset_cache(user_id, new_value=updated_user)
    """

    def __init__(
        self,
        ttl: int | float | None = None,
        key: str | None = None,
        no_self: bool = False,
        stampede_protection: bool = True,
        early_recompute_beta: float = 1.0,
    ) -> None:
        self.ttl = ttl
        self.key = key
        self.no_self = no_self
        self.stampede_protection = stampede_protection
        self.early_recompute_beta = early_recompute_beta
        self.func: Callable[..., Awaitable[T]] | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Union["cached", Awaitable[T]]:
        if self.func is None:
            # First call - receiving the decorated function
            self.func = args[0]
            functools.update_wrapper(self, self.func)
            return self
        # Subsequent calls - executing the cached function
        return self._get_or_set(*args, **kwargs)

    async def _get_or_set(self, *args: Any, **kwargs: Any) -> Any:
        """Get value from cache or compute and store it."""
        if self.func is None:
            raise RuntimeError("cached decorator not properly initialized")

        key = self._build_key(*args, **kwargs)

        cached_data = await aredis.get(key)
        if cached_data is not None:
            value, expiry, is_valid = _deserialize(cached_data)
            if is_valid:
                # Check for probabilistic early recomputation
                if self.early_recompute_beta > 0 and _should_early_recompute(expiry, self.early_recompute_beta):
                    log.debug("Cached: PER triggered early recomputation", key=key)
                    asyncio.ensure_future(self._recompute_and_store(key, *args, **kwargs))
                return value

        # Cache miss - compute value (with optional stampede protection)
        if self.stampede_protection:
            return await self._get_or_set_with_lock(key, *args, **kwargs)

        result = await self.func(*args, **kwargs)
        asyncio.ensure_future(set_value(key, result, ttl=self.ttl))
        log.debug("Cached: writing new data", key=key)
        return result

    async def _get_or_set_with_lock(self, key: str, *args: Any, **kwargs: Any) -> Any:
        """Compute value with per-key lock to prevent stampede.

        Only one coroutine will recompute the value; others wait for the lock
        and then re-check the cache.
        """
        if self.func is None:
            raise RuntimeError("cached decorator not properly initialized")

        entry = _lock_registry.acquire_entry(key)
        try:
            async with entry.lock:
                # Re-check cache after acquiring lock — another coroutine may
                # have already filled it while we were waiting.
                cached_data = await aredis.get(key)
                if cached_data is not None:
                    value, _expiry, is_valid = _deserialize(cached_data)
                    if is_valid:
                        return value

                # Still a miss — we are the one to recompute
                result = await self.func(*args, **kwargs)
                await set_value(key, result, ttl=self.ttl)
                log.debug("Cached: writing new data (lock holder)", key=key)
                return result
        finally:
            _lock_registry.release_entry(key)

    async def _recompute_and_store(self, key: str, *args: Any, **kwargs: Any) -> None:
        """Recompute a value in the background and update the cache.

        Used by PER to refresh cache entries before they expire. Errors are
        logged and swallowed to avoid disrupting the caller.
        """
        if self.func is None:
            return
        try:
            result = await self.func(*args, **kwargs)
            await set_value(key, result, ttl=self.ttl)
            log.debug("Cached: PER background refresh complete", key=key)
        except Exception as exc:
            log.warning(
                "Cached: PER background refresh failed",
                key=key,
                error=str(exc),
            )

    def _build_key(self, *args: Any, **kwargs: Any) -> str:
        """Build a unique cache key from function name and arguments."""
        if self.func is None:
            raise RuntimeError("cached decorator not properly initialized")

        ordered_kwargs = sorted(kwargs.items())

        func_module = getattr(self.func, "__module__", "") or ""
        func_name = getattr(self.func, "__name__", "unknown")
        base_key = self.key if self.key else func_module + func_name
        args_key = str(args[1:] if self.no_self else args)

        new_key = base_key + args_key
        if ordered_kwargs:
            new_key += str(ordered_kwargs)

        return new_key

    async def reset_cache(self, *args: Any, new_value: Any = None, **kwargs: Any) -> int | None:
        """Reset cache for specific arguments, optionally setting a new value.

        Args:
            *args: Same arguments as the cached function
            new_value: Optional new value to cache
            **kwargs: Same keyword arguments as the cached function

        Returns:
            Number of keys deleted, or None if new_value was set
        """
        key = self._build_key(*args, **kwargs)
        if new_value is not None:
            await set_value(key, new_value, ttl=self.ttl)
            return None
        return await aredis.delete(key)
