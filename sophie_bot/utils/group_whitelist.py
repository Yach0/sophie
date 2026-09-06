from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from time import monotonic
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import WatchError


if TYPE_CHECKING:
    from sophie_bot.db.models.group_user_whitelist import GroupUserWhitelistModel
    from sophie_bot.utils.feature_flags import FeatureType

GROUP_USER_WHITELIST_CACHE_KEY_PREFIX: Final[str] = "sophie:group_user_whitelist"
GROUP_USER_WHITELIST_CACHE_TTL_SECONDS: Final[int] = 60
GROUP_USER_WHITELIST_LOCK_TIMEOUT_SECONDS: Final[int] = 10
GROUP_USER_WHITELIST_LOCK_RETRY_SECONDS: Final[float] = 0.01


def _whitelist_model() -> type[GroupUserWhitelistModel]:
    # Deferred because importing any db.models submodule initializes the model
    # package, whose moderation models import this utility during startup.
    from sophie_bot.db.models.group_user_whitelist import GroupUserWhitelistModel

    return GroupUserWhitelistModel


async def is_enabled(feature: FeatureType, *, chat_tid: int, redis: Redis) -> bool:
    # feature_flags also imports a db.models submodule, so it must follow the
    # same dependency-safe boundary as the whitelist model above.
    from sophie_bot.utils.feature_flags import is_enabled as feature_is_enabled

    return await feature_is_enabled(feature, chat_tid=chat_tid, redis=redis)


def group_user_whitelist_cache_key(chat_tid: int, user_tid: int) -> str:
    return f"{GROUP_USER_WHITELIST_CACHE_KEY_PREFIX}:{chat_tid}:{user_tid}"


def _group_user_whitelist_lock_key(chat_tid: int, user_tid: int) -> str:
    return f"{GROUP_USER_WHITELIST_CACHE_KEY_PREFIX}:lock:{chat_tid}:{user_tid}"


async def _release_group_user_whitelist_lock(lock_key: str, owner: str, *, redis: Redis) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(lock_key)
            if await pipe.get(lock_key) != owner.encode():
                await pipe.unwatch()
                return
            pipe.multi()
            pipe.delete(lock_key)
            await pipe.execute()
        except WatchError:
            # The lock expired or changed owners between WATCH and EXEC. It is no
            # longer ours to release.
            return


@asynccontextmanager
async def _group_user_whitelist_lock(lock_key: str, *, redis: Redis) -> AsyncIterator[str]:
    owner = uuid4().hex
    deadline = monotonic() + GROUP_USER_WHITELIST_LOCK_TIMEOUT_SECONDS

    while not await redis.set(
        lock_key,
        owner,
        nx=True,
        ex=GROUP_USER_WHITELIST_LOCK_TIMEOUT_SECONDS,
    ):
        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError(f"Timed out acquiring group whitelist lock {lock_key}")
        await asyncio.sleep(min(GROUP_USER_WHITELIST_LOCK_RETRY_SECONDS, remaining_seconds))

    try:
        yield owner
    finally:
        await _release_group_user_whitelist_lock(lock_key, owner, redis=redis)


@asynccontextmanager
async def _group_user_whitelist_locks(
    *memberships: tuple[int, int],
    redis: Redis,
) -> AsyncIterator[dict[str, str]]:
    lock_keys = sorted({_group_user_whitelist_lock_key(chat_tid, user_tid) for chat_tid, user_tid in memberships})
    lock_owners: dict[str, str] = {}
    async with AsyncExitStack() as stack:
        for lock_key in lock_keys:
            lock_owners[lock_key] = await stack.enter_async_context(
                _group_user_whitelist_lock(lock_key, redis=redis)
            )
        yield lock_owners


async def _cache_membership_if_lock_owned(
    lock_key: str,
    owner: str,
    cache_key: str,
    value: bytes,
    *,
    redis: Redis,
) -> bool:
    async with redis.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(lock_key)
            if await pipe.get(lock_key) != owner.encode():
                await pipe.unwatch()
                return False
            pipe.multi()
            pipe.set(cache_key, value, ex=GROUP_USER_WHITELIST_CACHE_TTL_SECONDS)
            await pipe.execute()
        except WatchError:
            return False
    return True


async def invalidate_group_user_whitelist_cache(chat_tid: int, user_tid: int, *, redis: Redis) -> None:
    await redis.delete(group_user_whitelist_cache_key(chat_tid, user_tid))


async def add_user_to_group_whitelist(chat_tid: int, user_tid: int, *, redis: Redis) -> bool:
    async with _group_user_whitelist_locks((chat_tid, user_tid), redis=redis):
        added = await _whitelist_model().add_user(chat_tid, user_tid)
        await invalidate_group_user_whitelist_cache(chat_tid, user_tid, redis=redis)
        return added


async def remove_user_from_group_whitelist(chat_tid: int, user_tid: int, *, redis: Redis) -> bool:
    async with _group_user_whitelist_locks((chat_tid, user_tid), redis=redis):
        removed = await _whitelist_model().remove_user(chat_tid, user_tid)
        await invalidate_group_user_whitelist_cache(chat_tid, user_tid, redis=redis)
        return removed


async def migrate_group_user_whitelist_chat(old_chat_tid: int, new_chat_tid: int, *, redis: Redis) -> None:
    whitelist_model = _whitelist_model()
    entries = await whitelist_model.find({"chat_tid": old_chat_tid}).to_list()
    for entry in entries:
        user_tid = entry.user_tid
        async with _group_user_whitelist_locks((old_chat_tid, user_tid), (new_chat_tid, user_tid), redis=redis):
            old_entry = await whitelist_model.find_one({"chat_tid": old_chat_tid, "user_tid": user_tid})
            if old_entry is not None:
                await whitelist_model.add_user(new_chat_tid, user_tid)
                await old_entry.delete()

            async with redis.pipeline(transaction=True) as pipe:
                pipe.delete(group_user_whitelist_cache_key(old_chat_tid, user_tid))
                pipe.delete(group_user_whitelist_cache_key(new_chat_tid, user_tid))
                await pipe.execute()


async def is_user_group_whitelisted(chat_tid: int, user_tid: int, *, redis: Redis) -> bool:
    """Return whether a user is exempt from automated moderation in a group."""

    if not await is_enabled("group_user_whitelist", chat_tid=chat_tid, redis=redis):
        return False

    cache_key = group_user_whitelist_cache_key(chat_tid, user_tid)
    lock_key = _group_user_whitelist_lock_key(chat_tid, user_tid)
    cached_membership = await redis.get(cache_key)
    if cached_membership is not None:
        return cached_membership == b"1"

    async with _group_user_whitelist_locks((chat_tid, user_tid), redis=redis) as lock_owners:
        cached_membership = await redis.get(cache_key)
        if cached_membership is not None:
            return cached_membership == b"1"

        entry = await _whitelist_model().find_one({"chat_tid": chat_tid, "user_tid": user_tid})
        is_whitelisted = entry is not None
        await _cache_membership_if_lock_owned(
            lock_key,
            lock_owners[lock_key],
            cache_key,
            b"1" if is_whitelisted else b"0",
            redis=redis,
        )
        return is_whitelisted
