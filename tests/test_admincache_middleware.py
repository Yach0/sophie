from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.constants import CACHE_ADMIN_TTL_SECONDS
from sophie_bot.modules.utils_ import admin
from sophie_bot.modules.utils_.admin import (
    REFRESH_MARKER_PREFIX,
    ensure_admin_snapshot,
)


class _AdminQuery:
    def __init__(self, oldest_admin: object | None) -> None:
        self.oldest_admin = oldest_admin

    def sort(self, _field: object) -> _AdminQuery:
        return self

    async def first_or_none(self) -> object | None:
        return self.oldest_admin


@pytest.mark.asyncio
async def test_naive_fresh_admin_timestamp_does_not_refresh(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
    db_init: object,
) -> None:
    _ = db_init
    oldest_admin = SimpleNamespace(
        last_updated=datetime.now(UTC).replace(tzinfo=None),
    )
    monkeypatch.setattr(
        admin.ChatAdminModel,
        "find",
        lambda *_args, **_kwargs: _AdminQuery(oldest_admin),
    )
    refresh = AsyncMock()
    monkeypatch.setattr(admin, "refresh_admin_snapshot", refresh)
    chat = SimpleNamespace(iid=PydanticObjectId(), tid=-1001234567890)

    await ensure_admin_snapshot(chat, bot=object(), redis=test_redis)

    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_admin_snapshot_is_refreshed_once_per_ttl(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
    db_init: object,
) -> None:
    _ = db_init
    monkeypatch.setattr(
        admin.ChatAdminModel,
        "find",
        lambda *_args, **_kwargs: _AdminQuery(None),
    )
    refresh = AsyncMock()
    monkeypatch.setattr(admin, "refresh_admin_snapshot", refresh)
    chat = SimpleNamespace(iid=PydanticObjectId(), tid=-1001234567890)
    bot = object()

    for _call_index in range(3):
        await ensure_admin_snapshot(chat, bot=bot, redis=test_redis)

    refresh.assert_awaited_once_with(chat, bot=bot)
    ttl = await test_redis.ttl(f"{REFRESH_MARKER_PREFIX}{chat.iid}")
    assert 0 < ttl <= CACHE_ADMIN_TTL_SECONDS


@pytest.mark.asyncio
async def test_stale_admin_snapshot_refreshes(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
    db_init: object,
) -> None:
    _ = db_init
    oldest_admin = SimpleNamespace(
        last_updated=(
            datetime.now(UTC)
            - timedelta(seconds=CACHE_ADMIN_TTL_SECONDS + 60)
        ).replace(tzinfo=None),
    )
    monkeypatch.setattr(
        admin.ChatAdminModel,
        "find",
        lambda *_args, **_kwargs: _AdminQuery(oldest_admin),
    )
    refresh = AsyncMock()
    monkeypatch.setattr(admin, "refresh_admin_snapshot", refresh)
    chat = SimpleNamespace(iid=PydanticObjectId(), tid=-1001234567890)
    bot = object()

    await ensure_admin_snapshot(chat, bot=bot, redis=test_redis)

    refresh.assert_awaited_once_with(chat, bot=bot)
