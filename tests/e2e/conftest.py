"""E2E test fixtures and utilities.

This module provides fixtures specifically for end-to-end testing
using aiogram-test-framework with mocked services.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from aiogram import Dispatcher, Router
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_test_framework import TestClient
from aiogram_test_framework.mock_bot import MockBot
from aiogram_test_framework.request_capture import RequestCapture
from ass_tg.middleware import ArgsMiddleware
from fakeredis import FakeAsyncRedis

from sophie_bot.config import CONFIG
from sophie_bot.middlewares import (
    ConnectionsMiddleware,
    DisablingMiddleware,
    LocalizationMiddleware,
    SaveChatsMiddleware,
)
from sophie_bot.modules import load_modules
from sophie_bot.services.bot import get_bot_runtime
from sophie_bot.services.i18n import i18n

# Importing db_fixture patches pymongo.AsyncMongoClient. It must precede every sophie_bot
# import below, because sophie_bot.services.db builds a client at module level.
from tests.utils.db_fixture import (
    cleanup_beanie,
)


@pytest_asyncio.fixture(scope="session")
async def test_dispatcher(db_init: Any) -> AsyncGenerator[Dispatcher]:
    """Create a test dispatcher with all modules loaded.

    This fixture creates a fresh Dispatcher and loads all Sophie modules
    for end-to-end testing.
    """
    # Create fake redis for FSM storage
    fake_redis = FakeAsyncRedis(
        decode_responses=False,
        single_connection_client=True,
    )

    storage = RedisStorage(redis=fake_redis, key_builder=DefaultKeyBuilder(prefix="test_fsm"))

    # Create dispatcher with memory isolation for tests
    dp = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())

    # Register middlewares in the same order as the main app
    dp.update.middleware(LocalizationMiddleware(i18n))
    dp.update.outer_middleware(SaveChatsMiddleware())
    dp.update.middleware(ConnectionsMiddleware())
    dp.message.middleware(DisablingMiddleware())
    dp.message.middleware(ArgsMiddleware(i18n=i18n))

    # Load all modules
    await load_modules(
        dp,
        to_load=CONFIG.modules_load,
        to_not_load=CONFIG.modules_not_load,
    )

    yield dp

    # Cleanup
    await storage.close()
    await fake_redis.aclose()


@pytest_asyncio.fixture
async def test_client(test_dispatcher: Dispatcher) -> AsyncGenerator[TestClient]:
    """Create a test client for aiogram testing.

    This fixture provides a TestClient from aiogram-test-framework
    that can be used to simulate user interactions with the bot.
    """
    # Create request capture
    capture = RequestCapture()

    # Create mock bot
    mock_bot = MockBot(
        capture=capture,
        token=CONFIG.token,
        bot_id=CONFIG.bot_id,
        bot_username=CONFIG.username or "test_bot",
        bot_first_name="Sophie",
    )

    # Point the runtime at the test doubles. Handlers reach the bot and the dispatcher
    # through the `sophie_bot.services.bot.bot` / `.dp` runtime proxies (e.g. restriction
    # helpers calling `bot.ban_chat_member`), not through the dispatcher's own bot, so
    # without this the proxies would build a real AiohttpSession and attempt live Telegram
    # calls — nondeterministic "Event loop is closed" failures under the session loop.
    # Because they are proxies, tests never need to monkeypatch individual modules that
    # did `from sophie_bot.services.bot import bot`.
    runtime = get_bot_runtime()
    original_bot = runtime.bot
    original_dispatcher = runtime.dispatcher
    runtime.bot = mock_bot
    runtime.dispatcher = test_dispatcher

    # Create test client
    client = TestClient(dispatcher=test_dispatcher, bot=mock_bot, capture=capture)

    try:
        yield client
    finally:
        runtime.bot = original_bot
        runtime.dispatcher = original_dispatcher
        # The FSM store is a fakeredis instance of its own, separate from the global
        # `sophie_bot.services.redis.aredis` that tests/conftest.py flushes.
        await test_dispatcher.storage.redis.flushall()
        # Only reset captures/counters — do NOT call client.close() because it
        # disconnects the session-scoped dispatcher's router tree and emits
        # shutdown, which breaks all subsequent tests that reuse the dispatcher.
        client.reset()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(db_init: Any) -> AsyncGenerator[None]:
    """Give every e2e test an empty database.

    Without this the whole session shares one database and tests have to invent globally
    unique Telegram IDs to avoid inheriting each other's state — a convention that fails
    silently when two files pick the same number.
    """
    yield

    await cleanup_beanie()


@pytest_asyncio.fixture
async def extra_router(test_dispatcher: Dispatcher) -> AsyncGenerator[Any]:
    """Attach a test-only router to the session dispatcher for one test.

    Returns a callable taking the router to include; the router is detached on teardown so
    its handlers cannot fire in unrelated tests.
    """
    attached: list[Router] = []

    def include(router: Router) -> Router:
        test_dispatcher.include_router(router)
        attached.append(router)
        return router

    yield include

    for router in attached:
        test_dispatcher.sub_routers.remove(router)
        # aiogram has no `exclude_router`, and the public `parent_router` setter refuses to
        # take None — detaching has to go through the private attribute.
        router._parent_router = None
