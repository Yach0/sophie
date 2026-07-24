"""Redis-backed liveness heartbeats for long-running Sophie modes.

Each runtime-image mode (bot, scheduler) periodically writes a
`sophie:health:{instance_name}:{component}` key holding the last-beat unix
timestamp with a short TTL. The healthcheck CLI reads it back to decide whether
the process is still alive. The key is namespaced by `CONFIG.instance_name` so
co-hosted instances that share one redis and run the same mode (beta and stable
both run `MODE=bot`) cannot mask each other's liveness. Heartbeat writes must
never take a mode down, so redis failures are logged and swallowed.
"""

from __future__ import annotations

import asyncio
import time

from redis.exceptions import RedisError

from sophie_bot.config import CONFIG
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

HEARTBEAT_TTL_SECONDS = 45
HEARTBEAT_INTERVAL_SECONDS = 15

_KEY_PREFIX = "sophie:health:"


def _heartbeat_key(component: str) -> str:
    return f"{_KEY_PREFIX}{CONFIG.instance_name}:{component}"


async def write_heartbeat(component: str) -> None:
    """Write a fresh heartbeat timestamp for `component` with a TTL."""
    await aredis.set(_heartbeat_key(component), int(time.time()), ex=HEARTBEAT_TTL_SECONDS)


async def write_heartbeat_guarded(component: str) -> None:
    """Write a heartbeat, logging and swallowing redis errors so callers never fail.

    Used both by the background loop and by the scheduler job, so a redis outage
    surfaces as a warning rather than a crashed task or a recurring job exception.
    """
    try:
        await write_heartbeat(component)
    except RedisError as error:
        log.warning("Heartbeat write failed", component=component, error=str(error))


async def heartbeat_loop(component: str) -> None:
    """Write the heartbeat every interval forever; never crash on redis errors."""
    while True:
        await write_heartbeat_guarded(component)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def check_heartbeat(component: str, max_age_seconds: int) -> bool:
    """Return True if `component` has a heartbeat no older than `max_age_seconds`."""
    try:
        raw = await aredis.get(_heartbeat_key(component))
    except RedisError as error:
        log.warning("Heartbeat read failed", component=component, error=str(error))
        return False

    if raw is None:
        return False

    try:
        beat_ts = int(raw)
    except (TypeError, ValueError):
        return False

    return (int(time.time()) - beat_ts) <= max_age_seconds
