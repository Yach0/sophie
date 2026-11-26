from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import fields
from json import dumps
from time import time

from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from sophie_bot.modules.antispam.provider import ProtectionVerdict
from sophie_bot.utils.logger import log

VERDICT = TypeAdapter(ProtectionVerdict)


async def cached_verdict(
    redis: Redis,
    key: str,
    lookup: Callable[[], Awaitable[ProtectionVerdict]],
) -> ProtectionVerdict:
    try:
        payload = await redis.get(key)
        if payload:
            return VERDICT.validate_json(payload)
    except (RedisError, ValidationError):
        log.debug("Could not read anti-spam cache", key=key)

    verdict = await lookup()
    values = {item.name: getattr(verdict, item.name) for item in fields(verdict)}
    values["raw_metadata"] = dict(verdict.raw_metadata)
    ttl = 600 if verdict.matched else 1800
    expires_at = verdict.raw_metadata.get("expires_at")
    if isinstance(expires_at, int):
        ttl = min(ttl, int(expires_at - time()))
    if ttl <= 0:
        return verdict
    try:
        await redis.set(key, dumps(values), ex=ttl)
    except RedisError:
        log.debug("Could not write anti-spam cache", key=key)
    return verdict
