from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING, Any

from beanie import PydanticObjectId

from sophie_bot.db.models.federations import Federation
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

if TYPE_CHECKING:
    pass


class FederationCacheService:
    """Cache service for federation lookups to reduce database queries."""

    CACHE_PREFIX = "fed:"
    CACHE_TTL = 60  # seconds

    @staticmethod
    async def get_federation_by_id(fed_id: str) -> Optional[dict]:
        """Get federation from cache."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}id:{fed_id}"

        # Try cache first
        cached = await aredis.get(cache_key)
        if cached:
            try:
                federation_data = json.loads(cached)

                return federation_data
            except (json.JSONDecodeError, Exception) as e:
                log.warning("Failed to parse cached federation data", fed_id=fed_id, error=str(e))

        return None

    @staticmethod
    async def get_federation_for_chat(chat_iid: PydanticObjectId) -> Optional[dict]:
        """Get federation for chat from cache."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat:{chat_iid}"

        # Try cache first
        cached = await aredis.get(cache_key)
        if cached:
            try:
                federation_data = json.loads(cached)

                return federation_data
            except (json.JSONDecodeError, Exception) as e:
                log.warning("Failed to parse cached federation data", chat_iid=chat_iid, error=str(e))

        return None

    @staticmethod
    async def invalidate_federation(fed_id: str) -> None:
        """Invalidate cached federation data."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}id:{fed_id}"
        await aredis.delete(cache_key)

    @staticmethod
    async def invalidate_federation_for_chat(chat_iid: PydanticObjectId) -> None:
        """Invalidate cached federation for chat."""
        cache_key = f"{FederationCacheService.CACHE_PREFIX}chat:{chat_iid}"
        await aredis.delete(cache_key)

    @staticmethod
    async def _cache_federation(federation: Any) -> None:
        """Cache federation data."""
        # Handle both dict and model object
        if hasattr(federation, "model_dump"):
            data = federation.model_dump(mode="json")
        elif isinstance(federation, dict):
            data = federation
        else:
            # Manual mapping for Beanie documents if needed
            data = {
                "fed_name": getattr(federation, "fed_name", None),
                "fed_id": getattr(federation, "fed_id", None),
                "creator": getattr(federation, "creator", None),
                "chats": getattr(federation, "chats", []),
                "subscribed": getattr(federation, "subscribed", []),
                "admins": getattr(federation, "admins", []),
                "log_chat": getattr(federation, "log_chat", None),
            }

        cache_key = f"{FederationCacheService.CACHE_PREFIX}id:{data['fed_id']}"
        await aredis.set(cache_key, json.dumps(data), ex=FederationCacheService.CACHE_TTL)

        # Cache for each chat in federation too (by chat_iid)
        if isinstance(federation, Federation):
            for chat_iid in [c.to_ref() for c in federation.chats]:
                chat_cache_key = f"{FederationCacheService.CACHE_PREFIX}chat:{chat_iid}"
                await aredis.set(chat_cache_key, json.dumps(data), ex=FederationCacheService.CACHE_TTL)
