"""End-to-end fixtures backed by explicit application-owned test resources."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram import Dispatcher, Router
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_test_framework import TestClient
from aiogram_test_framework.mock_bot import MockBot
from aiogram_test_framework.request_capture import RequestCapture
from fakeredis import FakeAsyncRedis

from sophie_bot.config import CONFIG
from sophie_bot.db.cache.locale import LocaleStore
from sophie_bot.middlewares import enable_middlewares
from sophie_bot.modules import assemble_bot_modules, discover_modules, initialize_modules
from sophie_bot.modules.utils_.delayed_delete import DelayedDeletionService
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.db import DatabaseResources
from sophie_bot.services.i18n import i18n
from sophie_bot.utils.cached import RedisCache
from tests.e2e.helpers import TEST_BOT_USERNAME
from tests.utils.db_fixture import MOCK_MONGO, cleanup_beanie


@pytest_asyncio.fixture(scope="session")
async def test_services(db_init: Any) -> AsyncGenerator[ApplicationServices]:
    redis = FakeAsyncRedis(decode_responses=False, single_connection_client=True)
    capture = RequestCapture()
    bot = MockBot(
        capture=capture,
        token=CONFIG.token,
        bot_id=CONFIG.bot_id,
        bot_username=TEST_BOT_USERNAME,
        bot_first_name="Sophie",
    )
    bot.get_chat_administrators = AsyncMock(return_value=[])
    cache = RedisCache(redis)
    services = ApplicationServices(
        bot=bot,
        redis=redis,
        db=DatabaseResources(mongo=MOCK_MONGO, database=db_init, initialized=True),
        cache=cache,
        locales=LocaleStore(cache, i18n, CONFIG.default_locale),
        deletions=DelayedDeletionService(bot),
        modules=discover_modules(CONFIG.modules_load, CONFIG.modules_not_load),
        background_tasks=set(),
    )
    await initialize_modules(services)
    yield services
    await services.close()


@pytest_asyncio.fixture(scope="session")
async def test_dispatcher(
    test_services: ApplicationServices,
) -> AsyncGenerator[Dispatcher]:
    """Create one dispatcher with the production module and middleware ordering."""
    fsm_redis = FakeAsyncRedis(decode_responses=False, single_connection_client=True)
    storage = RedisStorage(
        redis=fsm_redis,
        key_builder=DefaultKeyBuilder(prefix="test_fsm"),
    )
    dispatcher = Dispatcher(
        storage=storage,
        events_isolation=SimpleEventIsolation(),
    )
    dispatcher.workflow_data["services"] = test_services
    await assemble_bot_modules(dispatcher, test_services)
    enable_middlewares(dispatcher, test_services, None)

    yield dispatcher

    await storage.close()


@pytest_asyncio.fixture
async def test_client(
    test_dispatcher: Dispatcher,
    test_services: ApplicationServices,
) -> AsyncGenerator[TestClient]:
    """Exercise handlers through the explicit MockBot owned by test services."""
    bot = test_services.bot
    capture = bot.capture
    client = TestClient(dispatcher=test_dispatcher, bot=bot, capture=capture)
    try:
        yield client
    finally:
        await test_dispatcher.storage.redis.flushall()
        await test_services.redis.flushall()
        client.reset()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(db_init: Any) -> AsyncGenerator[None]:
    """Give every E2E test an empty database."""
    yield
    await cleanup_beanie()


@pytest_asyncio.fixture
async def extra_router(test_dispatcher: Dispatcher) -> AsyncGenerator[Any]:
    """Attach and later detach a test-only router."""
    attached: list[Router] = []

    def include(router: Router) -> Router:
        test_dispatcher.include_router(router)
        attached.append(router)
        return router

    yield include

    for router in attached:
        test_dispatcher.sub_routers.remove(router)
        router._parent_router = None
