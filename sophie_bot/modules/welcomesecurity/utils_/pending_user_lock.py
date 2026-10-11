from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from time import monotonic
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import WatchError

WELCOME_SECURITY_PENDING_LOCK_TIMEOUT_SECONDS = 30
WELCOME_SECURITY_PENDING_LOCK_RETRY_SECONDS = 0.01


def _pending_user_lock_key(group_tid: int, user_tid: int) -> str:
    return f"sophie:welcomesecurity:pending-lock:{group_tid}:{user_tid}"


async def _release_pending_user_lock(lock_key: str, owner: str, *, redis: Redis) -> None:
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
            return


async def _renew_pending_user_lock(lock_key: str, owner: str, *, redis: Redis) -> None:
    lease_milliseconds = int(WELCOME_SECURITY_PENDING_LOCK_TIMEOUT_SECONDS * 1000)
    renewal_interval = WELCOME_SECURITY_PENDING_LOCK_TIMEOUT_SECONDS / 3
    while True:
        await asyncio.sleep(renewal_interval)
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(lock_key)
                if await pipe.get(lock_key) != owner.encode():
                    await pipe.unwatch()
                    return
                pipe.multi()
                pipe.pexpire(lock_key, lease_milliseconds)
                await pipe.execute()
            except WatchError:
                continue


@asynccontextmanager
async def pending_user_lock(group_tid: int, user_tid: int, *, redis: Redis) -> AsyncIterator[None]:
    lock_key = _pending_user_lock_key(group_tid, user_tid)
    owner = uuid4().hex
    deadline = monotonic() + WELCOME_SECURITY_PENDING_LOCK_TIMEOUT_SECONDS
    lease_milliseconds = int(WELCOME_SECURITY_PENDING_LOCK_TIMEOUT_SECONDS * 1000)

    while not await redis.set(
        lock_key,
        owner,
        nx=True,
        px=lease_milliseconds,
    ):
        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError(f"Timed out acquiring Welcome Security pending-user lock {lock_key}")
        await asyncio.sleep(min(WELCOME_SECURITY_PENDING_LOCK_RETRY_SECONDS, remaining_seconds))

    renewal_task = asyncio.create_task(_renew_pending_user_lock(lock_key, owner, redis=redis))
    try:
        yield
    finally:
        renewal_task.cancel()
        with suppress(asyncio.CancelledError):
            await renewal_task
        await _release_pending_user_lock(lock_key, owner, redis=redis)
