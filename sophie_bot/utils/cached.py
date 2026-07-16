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
import inspect
import math
import random
import time
from typing import Any, Awaitable, Callable, Generic, ParamSpec, TypeVar, cast

import ujson

from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

T = TypeVar("T")
P = ParamSpec("P")

# Sentinel value to distinguish cached None from cache miss
_NOT_SET_MARKER = "__sophie_not_set__"

# Fire-and-forget tasks are kept here until they finish; asyncio only holds a weak
# reference to a running task, so an unreferenced one can be garbage collected mid-flight.
_background_tasks: set[asyncio.Task[Any]] = set()


def _spawn(coro: Awaitable[Any]) -> None:
    """Run a coroutine in the background, keeping a strong reference until it completes."""
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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
    """Registry of per-key asyncio locks.

    Entries are reference counted: `release_entry` drops a key as soon as its last
    waiter is gone, so the registry never outgrows the set of in-flight recomputations.
    """

    def __init__(self) -> None:
        self._locks: dict[str, _LockEntry] = {}

    def acquire_entry(self, key: str) -> _LockEntry:
        """Get or create a lock entry for the given key, incrementing the waiter count."""
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


class CachedFunction(Generic[P, T]):
    """An async function whose results are cached in Redis.

    Created by the `cached` decorator; see its docstring for usage.
    """

    def __init__(
        self,
        func: Callable[P, Awaitable[T]],
        ttl: int | float | None,
        key: str | None,
        no_self: bool,
        stampede_protection: bool,
        early_recompute_beta: float,
    ) -> None:
        self.func = func
        self.signature = inspect.signature(func)
        self.ttl = ttl
        self.key = key
        self.no_self = no_self
        self.stampede_protection = stampede_protection
        self.early_recompute_beta = early_recompute_beta
        functools.update_wrapper(self, func)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Get value from cache or compute and store it."""
        key = self._build_key(*args, **kwargs)

        cached_data = await aredis.get(key)
        if cached_data is not None:
            value, expiry, is_valid = _deserialize(cached_data)
            if is_valid:
                # Check for probabilistic early recomputation
                if self.early_recompute_beta > 0 and _should_early_recompute(expiry, self.early_recompute_beta):
                    log.debug("Cached: PER triggered early recomputation", key=key)
                    _spawn(self._recompute_and_store(key, *args, **kwargs))
                return cast(T, value)

        # Cache miss - compute value (with optional stampede protection)
        if self.stampede_protection:
            return await self._get_or_set_with_lock(key, *args, **kwargs)

        result = await self.func(*args, **kwargs)
        _spawn(set_value(key, result, ttl=self.ttl))
        log.debug("Cached: writing new data", key=key)
        return result

    async def _get_or_set_with_lock(self, key: str, *args: P.args, **kwargs: P.kwargs) -> T:
        """Compute value with per-key lock to prevent stampede.

        Only one coroutine will recompute the value; others wait for the lock
        and then re-check the cache.
        """
        entry = _lock_registry.acquire_entry(key)
        try:
            async with entry.lock:
                # Re-check cache after acquiring lock — another coroutine may
                # have already filled it while we were waiting.
                cached_data = await aredis.get(key)
                if cached_data is not None:
                    value, _expiry, is_valid = _deserialize(cached_data)
                    if is_valid:
                        return cast(T, value)

                # Still a miss — we are the one to recompute
                result = await self.func(*args, **kwargs)
                await set_value(key, result, ttl=self.ttl)
                log.debug("Cached: writing new data (lock holder)", key=key)
                return result
        finally:
            _lock_registry.release_entry(key)

    async def _recompute_and_store(self, key: str, *args: P.args, **kwargs: P.kwargs) -> None:
        """Recompute a value in the background and update the cache.

        Used by PER to refresh cache entries before they expire. Errors are
        logged and swallowed to avoid disrupting the caller.
        """
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
        """Build a unique cache key from the function identity and its arguments.

        Arguments are bound to the signature first, so `f(x)` and `f(chat_iid=x)` produce the
        same key. Without that, a writer invalidating positionally would leave a reader that
        passes the same argument by keyword on a stale entry.
        """
        func_module = getattr(self.func, "__module__", "") or ""
        func_name = getattr(self.func, "__name__", "unknown")
        base_key = self.key if self.key else f"{func_module}:{func_name}"

        bound = self.signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = list(bound.arguments.items())
        if self.no_self:
            arguments = arguments[1:]

        # repr() rather than str(): it keeps values of different types distinguishable
        # (1 vs "1"), which str() would collapse into the same key.
        return base_key + "(" + ",".join(f"{name}={value!r}" for name, value in arguments) + ")"

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


class cached:  # noqa: N801
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

    def __call__(self, func: Callable[P, Awaitable[T]]) -> CachedFunction[P, T]:
        return CachedFunction(
            func,
            ttl=self.ttl,
            key=self.key,
            no_self=self.no_self,
            stampede_protection=self.stampede_protection,
            early_recompute_beta=self.early_recompute_beta,
        )
