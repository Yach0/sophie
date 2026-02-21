from __future__ import annotations

from typing import Optional

from beanie import PydanticObjectId

from sophie_bot.services.redis import aredis


class FederationCacheService:
    """Cache service for federation lookups to reduce database queries."""

    CACHE_PREFIX = "fed:"
    CACHE_TTL = 300  # seconds

    @staticmethod
    async def get_fed_id_for_chat(chat_iid: PydanticObjectId) -> Optional[str]:
        """Get federation ID for chat from cache."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_fed_id:{chat_iid}"
        cached = await aredis.get(cache_key)
        return cached.decode() if cached else None

    @staticmethod
    async def set_fed_id_for_chat(chat_iid: PydanticObjectId, fed_id: str) -> None:
        """Cache federation ID for chat."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_fed_id:{chat_iid}"
        await aredis.set(cache_key, fed_id, ex=FederationCacheService.CACHE_TTL)

    @staticmethod
    async def invalidate_federation_for_chat(chat_iid: PydanticObjectId) -> None:
        """Invalidate cached federation for chat."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat_fed_id:{chat_iid}"
        await aredis.delete(cache_key)

    @staticmethod
    async def get_user_ban_status(fed_id: str, user_tid: int) -> Optional[bool]:
        """Get cached negative ban status."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}ban_status:{fed_id}:{user_tid}"
        cached = await aredis.get(cache_key)
        if cached:
            return cached.decode() == "1"
        return None

    @staticmethod
    async def set_user_ban_status(fed_id: str, user_tid: int, is_banned: bool) -> None:
        """Cache negative ban status."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}ban_status:{fed_id}:{user_tid}"
        await aredis.set(cache_key, "1" if is_banned else "0", ex=FederationCacheService.CACHE_TTL)
