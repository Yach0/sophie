from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.constants import CACHE_ADMIN_TTL_SECONDS
from sophie_bot.middlewares.admincache import AdmincacheMiddleware


@pytest.mark.asyncio
async def test_is_cache_stale_handles_naive_last_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = AdmincacheMiddleware()
    chat_iid = PydanticObjectId()
    oldest_admin = SimpleNamespace(
        last_updated=(datetime.now(timezone.utc) - timedelta(seconds=CACHE_ADMIN_TTL_SECONDS + 60)).replace(
            tzinfo=None
        ),
    )
    monkeypatch.setattr(middleware, "_get_oldest_admin", AsyncMock(return_value=oldest_admin))

    is_stale = await middleware._is_cache_stale(chat_iid)

    assert is_stale is True


def test_ensure_utc_datetime_adds_utc_to_naive_values() -> None:
    naive_dt = datetime(2026, 5, 4, 20, 30)

    normalized = AdmincacheMiddleware._ensure_utc_datetime(naive_dt)

    assert normalized.tzinfo == timezone.utc
