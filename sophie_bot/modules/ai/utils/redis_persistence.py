from __future__ import annotations

from collections.abc import Awaitable, Callable

from redis.exceptions import ConnectionError, TimeoutError, WatchError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_fixed


async def persist_redis_commit[Result](commit: Callable[[], Awaitable[Result]]) -> Result:
    """Retry only a frozen, idempotent Redis commit after successful delivery.

    The storage owner prepares the payload once and checks its original reset epoch (and
    lease, when applicable) on every attempt. Generation, delivery and completion stay outside.
    """
    return await AsyncRetrying(
        retry=retry_if_exception_type((ConnectionError, TimeoutError, WatchError)),
        stop=stop_after_attempt(3),
        wait=wait_fixed(0.1),
        reraise=True,
    )(commit)
