from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.constants import CACHE_ADMIN_TTL_SECONDS
from sophie_bot.middlewares import admincache
from sophie_bot.middlewares.admincache import REFRESH_MARKER_PREFIX, AdmincacheMiddleware
from sophie_bot.services.redis import aredis


@pytest.mark.asyncio
async def test_is_cache_stale_handles_naive_last_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = AdmincacheMiddleware()
    chat_iid = PydanticObjectId()
    oldest_admin = SimpleNamespace(
        last_updated=(datetime.now(UTC) - timedelta(seconds=CACHE_ADMIN_TTL_SECONDS + 60)).replace(
            tzinfo=None
        ),
    )
    monkeypatch.setattr(middleware, "_get_oldest_admin", AsyncMock(return_value=oldest_admin))

    is_stale = await middleware._is_cache_stale(chat_iid)

    assert is_stale is True


@pytest.mark.asyncio
async def test_empty_admin_cache_is_refreshed_only_once_per_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refresh that persists no admins must not re-trigger getChatAdministrators on the next update.

    update_chat_members skips admins that have no ChatModel, so a fresh group's cache stays empty and
    every subsequent update used to cost another API call.
    """
    middleware = AdmincacheMiddleware()
    chat_iid = PydanticObjectId()
    chat_db = SimpleNamespace(tid=-1001234567890, iid=chat_iid)
    await aredis.delete(f"{REFRESH_MARKER_PREFIX}{chat_iid}")

    # The cache stays empty: this mirrors update_chat_members saving nothing for admins without a ChatModel.
    monkeypatch.setattr(middleware, "_get_oldest_admin", AsyncMock(return_value=None))
    update_chat_members_mock = AsyncMock()
    monkeypatch.setattr(admincache, "update_chat_members", update_chat_members_mock)

    for _call in range(3):
        await middleware._refresh_cache_if_needed(MagicMock(), {"chat_db": chat_db})

    update_chat_members_mock.assert_awaited_once_with(chat_db)


@pytest.mark.asyncio
async def test_refresh_is_claimed_once_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    await aredis.delete(f"{REFRESH_MARKER_PREFIX}{chat_iid}")

    assert await AdmincacheMiddleware._claim_refresh(chat_iid) is True
    assert await AdmincacheMiddleware._claim_refresh(chat_iid) is False
    assert await aredis.ttl(f"{REFRESH_MARKER_PREFIX}{chat_iid}") <= CACHE_ADMIN_TTL_SECONDS


def test_ensure_utc_datetime_adds_utc_to_naive_values() -> None:
    naive_dt = datetime(2026, 5, 4, 20, 30)  # noqa: DTZ001  # intentionally naive to test UTC normalization

    normalized = AdmincacheMiddleware._ensure_utc_datetime(naive_dt)

    assert normalized.tzinfo == UTC
