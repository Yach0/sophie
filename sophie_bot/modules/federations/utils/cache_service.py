from __future__ import annotations

from beanie import PydanticObjectId
from redis.asyncio import Redis


def _decode_redis_value(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class FederationCacheService:
    """Cache service for federation lookups to reduce database queries."""

    CACHE_PREFIX = "fed:"
    CACHE_TTL = 300  # 5 minutes
    STATS_TTL = 3600  # 1 hour

    @staticmethod
    async def get_fed_id_for_chat(chat_iid: PydanticObjectId, *, redis: Redis) -> str | None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_fed_id:{chat_iid}"
        cached = await redis.get(cache_key)
        return _decode_redis_value(cached) if cached else None

    @staticmethod
    async def set_fed_id_for_chat(chat_iid: PydanticObjectId, fed_id: str, *, redis: Redis) -> None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_fed_id:{chat_iid}"
        await redis.set(cache_key, fed_id, ex=FederationCacheService.CACHE_TTL)

    @staticmethod
    async def invalidate_federation_for_chat(chat_iid: PydanticObjectId, *, redis: Redis) -> None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_fed_id:{chat_iid}"
        await redis.delete(cache_key)

    @staticmethod
    async def get_user_ban_status(fed_id: str, user_tid: int, *, redis: Redis) -> bool | None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}ban_status:{fed_id}:{user_tid}"
        cached = await redis.get(cache_key)
        if cached:
            return _decode_redis_value(cached) == "1"
        return None

    @staticmethod
    async def set_user_ban_status(
        fed_id: str,
        user_tid: int,
        is_banned: bool,
        *,
        redis: Redis,
    ) -> None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}ban_status:{fed_id}:{user_tid}"
        await redis.set(
            cache_key,
            "1" if is_banned else "0",
            ex=FederationCacheService.CACHE_TTL,
        )

    # NEW: Stats Caching
    @staticmethod
    async def get_ban_count(fed_id: str, *, redis: Redis) -> int | None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}ban_count:{fed_id}"
        cached = await redis.get(cache_key)
        return int(cached) if cached else None

    @staticmethod
    async def set_ban_count(fed_id: str, count: int, *, redis: Redis) -> None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}ban_count:{fed_id}"
        await redis.set(cache_key, count, ex=FederationCacheService.STATS_TTL)

    @staticmethod
    async def incr_ban_count(fed_id: str, amount: int = 1, *, redis: Redis) -> None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}ban_count:{fed_id}"
        if await redis.exists(cache_key):
            await redis.incrby(cache_key, amount)

    @staticmethod
    async def get_chat_count(fed_id: str, *, redis: Redis) -> int | None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_count:{fed_id}"
        cached = await redis.get(cache_key)
        return int(cached) if cached else None

    @staticmethod
    async def set_chat_count(fed_id: str, count: int, *, redis: Redis) -> None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_count:{fed_id}"
        await redis.set(cache_key, count, ex=FederationCacheService.STATS_TTL)

    @staticmethod
    async def incr_chat_count(fed_id: str, amount: int = 1, *, redis: Redis) -> None:
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_count:{fed_id}"
        if await redis.exists(cache_key):
            await redis.incrby(cache_key, amount)
