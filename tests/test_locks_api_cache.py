from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.locks.api.put import set_locked_types
from sophie_bot.modules.locks.api.schemas import LocksPayload
from sophie_bot.modules.locks.utils.cache import CACHE_KEY_PREFIX, set_cached_locks
from sophie_bot.services.redis import aredis

CHAT_ID = -1001234567890


async def _group() -> ChatModel:
    return await ChatModel(
        tid=CHAT_ID,
        type=ChatType.supergroup,
        first_name_or_title="Test group",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()


@pytest.mark.asyncio
async def test_rest_lock_update_invalidates_the_locks_cache(db_init: Any) -> None:
    """PUT /locks/locked/{chat_iid} must drop the Redis cache the enforcer reads.

    Without invalidation the old lock set stays live for up to the 300s cache TTL.
    """
    chat = await _group()
    await set_cached_locks(CHAT_ID, {"url"})

    response = await set_locked_types(chat=chat, payload=LocksPayload(locked=["sticker"]), user=MagicMock())

    assert response.locked == ["sticker"]
    assert await aredis.get(f"{CACHE_KEY_PREFIX}{CHAT_ID}") is None
