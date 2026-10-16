from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from fakeredis import FakeAsyncRedis

from sophie_bot.utils import cached as cached_utils
from sophie_bot.utils.cached import Cached, RedisCache


def _cache_key(argument: str) -> str:
    return f"cache-contract:{argument}"


@pytest.fixture
async def cache(test_redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[RedisCache]:
    monkeypatch.setattr(cached_utils.random, "random", lambda: 1.0)
    owner = RedisCache(test_redis)
    yield owner
    await owner.close()


@pytest.mark.parametrize("value", (None, "ru_RU"))
async def test_cache_hits_include_negative_results(cache: RedisCache, value: str | None) -> None:
    load = AsyncMock(return_value=value)
    lookup = Cached[str, str | None](cache, load, ttl=60, key=_cache_key)

    assert await lookup("first") == value
    assert await lookup("first") == value
    load.assert_awaited_once_with("first")


@pytest.mark.parametrize("ttl", (None, 60))
async def test_cache_lifetime_and_reload_after_expiry(
    cache: RedisCache,
    test_redis: FakeAsyncRedis,
    ttl: int | None,
) -> None:
    load = AsyncMock(return_value="old")
    lookup = Cached[str, str](cache, load, ttl=ttl, key=_cache_key)

    assert await lookup("chat") == "old"
    remaining_ttl = await test_redis.ttl(_cache_key("chat"))
    if ttl is None:
        assert remaining_ttl == -1
    else:
        assert 0 < remaining_ttl <= ttl

    await test_redis.expire(_cache_key("chat"), 0)
    load.return_value = "new"

    assert await lookup("chat") == "new"
    assert load.await_count == 2


@pytest.mark.parametrize("value", (None, "ru_RU"))
async def test_concurrent_misses_load_each_key_once(cache: RedisCache, value: str | None) -> None:
    async def slow_load(_argument: str) -> str | None:
        await asyncio.sleep(0)
        return value

    load = AsyncMock(side_effect=slow_load)
    lookup = Cached[str, str | None](cache, load, ttl=60, key=_cache_key)

    results = await asyncio.gather(*(lookup("chat") for _request in range(12)))

    assert results == [value] * 12
    load.assert_awaited_once_with("chat")


async def test_loading_one_key_does_not_block_another(cache: RedisCache) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def load(argument: str) -> str:
        if argument == "first":
            first_started.set()
            await release_first.wait()
        return argument

    lookup = Cached[str, str](cache, load, ttl=60, key=_cache_key)
    first_task = asyncio.create_task(lookup("first"))
    try:
        await asyncio.wait_for(first_started.wait(), timeout=5)
        assert await asyncio.wait_for(lookup("second"), timeout=5) == "second"
    finally:
        release_first.set()
        assert await first_task == "first"


async def test_probabilistic_refresh_returns_cached_value_while_reloading(
    cache: RedisCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = AsyncMock(return_value="old")
    lookup = Cached[str, str](cache, load, ttl=60, key=_cache_key)
    assert await lookup("chat") == "old"
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def refresh(_argument: str) -> str:
        refresh_started.set()
        await release_refresh.wait()
        return "new"

    load.side_effect = refresh
    monkeypatch.setattr(cached_utils.random, "random", lambda: 0.0)

    assert await lookup("chat") == "old"
    await asyncio.wait_for(refresh_started.wait(), timeout=5)
    monkeypatch.setattr(cached_utils.random, "random", lambda: 1.0)
    release_refresh.set()

    async with asyncio.timeout(5):
        while await lookup("chat") != "new":
            await asyncio.sleep(0)
    assert load.await_count == 2


async def test_shutdown_cancels_refresh_without_closing_redis(
    cache: RedisCache,
    test_redis: FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = AsyncMock(return_value="old")
    lookup = Cached[str, str](cache, load, ttl=60, key=_cache_key)
    assert await lookup("chat") == "old"
    refresh_started = asyncio.Event()
    refresh_cancelled = asyncio.Event()

    async def refresh(_argument: str) -> str:
        refresh_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            refresh_cancelled.set()
        return "new"

    load.side_effect = refresh
    monkeypatch.setattr(cached_utils.random, "random", lambda: 0.0)

    assert await lookup("chat") == "old"
    await asyncio.wait_for(refresh_started.wait(), timeout=5)
    await cache.close()

    assert refresh_cancelled.is_set()
    assert await test_redis.ping()
    monkeypatch.setattr(cached_utils.random, "random", lambda: 1.0)
    assert await lookup("chat") == "old"
    assert load.await_count == 2


@pytest.mark.parametrize("serialized", ("not-json", '{"missing_value": "old"}'))
async def test_invalid_entries_are_replaced(
    cache: RedisCache,
    test_redis: FakeAsyncRedis,
    serialized: str,
) -> None:
    await test_redis.set(_cache_key("chat"), serialized)
    load = AsyncMock(return_value="new")
    lookup = Cached[str, str](cache, load, ttl=60, key=_cache_key)

    assert await lookup("chat") == "new"
    assert await lookup("chat") == "new"
    load.assert_awaited_once_with("chat")
