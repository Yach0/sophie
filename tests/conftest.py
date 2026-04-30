"""Global pytest fixtures for Sophie Bot tests.

This module provides fixtures for both unit tests and e2e tests.
For e2e tests, it sets up mocked MongoDB (via mongomock) and Redis (via fakeredis)
so tests can run without external services.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import mistralai.client.httpclient
import mistralai.client.sdk
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_test_framework import TestClient
from fakeredis import FakeAsyncRedis

from sophie_bot.config import CONFIG
from sophie_bot.modules import load_modules
from sophie_bot.utils.i18n import I18nNew
from tests.utils.db_fixture import (
    cleanup_beanie,
    create_mock_mongo,
    initialize_beanie,
)

if TYPE_CHECKING:
    pass


# Set testing environment
os.environ["TESTING"] = "1"

# Monkey patch mistralai's close_clients to avoid log spam during shutdown
# caused by asyncio.run() creating a new loop and logging "Using selector: EpollSelector"
# when the logging system might be partially closed.


def _safe_close_clients(
    owner: Any,
    sync_client: Any,
    sync_supplied: bool,
    async_client: Any,
    async_supplied: bool,
) -> None:
    if sync_client and not sync_supplied:
        sync_client.close()

    if async_client and not async_supplied:
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(async_client.aclose())
            else:
                asyncio.run_coroutine_threadsafe(async_client.aclose(), loop)
        except Exception:
            pass

    owner.client = None
    owner.async_client = None


mistralai.client.httpclient.close_clients = _safe_close_clients
mistralai.client.sdk.close_clients = _safe_close_clients


@pytest.fixture(scope="session", autouse=True)
def i18n_context() -> Any:
    """Provide i18n context for all tests."""
    i18n = I18nNew(path="locales")
    from ass_tg.i18n import gettext_ctx

    token = gettext_ctx.set(i18n)

    with i18n.context():
        yield i18n

    gettext_ctx.reset(token)


@pytest.fixture(scope="session")
async def mock_mongo() -> AsyncGenerator[Any, None]:
    """Create a mocked MongoDB client for e2e tests.

    This fixture patches pymongo.AsyncMongoClient to use our AsyncMongoMockClient
    which wraps mongomock and provides async compatibility for Beanie 2.0.
    """
    mock_client = create_mock_mongo()

    # Patch AsyncMongoClient at the module level
    with patch("pymongo.AsyncMongoClient", return_value=mock_client):
        with patch("sophie_bot.services.db.async_mongo", mock_client):
            yield mock_client

    await mock_client.aclose()


@pytest.fixture(scope="session")
async def db_init(mock_mongo: Any) -> AsyncGenerator[Any, None]:
    """Initialize Beanie with mocked MongoDB.

    This fixture sets up Beanie ODM with all models using the mocked MongoDB.
    """
    database = await initialize_beanie(mock_mongo)

    yield database

    await cleanup_beanie()


@pytest.fixture(scope="session")
async def test_dispatcher() -> AsyncGenerator[Dispatcher, None]:
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

    # Load all modules
    await load_modules(
        dp,
        to_load=CONFIG.modules_load,
        to_not_load=CONFIG.modules_not_load,
    )

    yield dp

    # Cleanup
    await storage.close()
    await fake_redis.close()


@pytest.fixture
async def test_client(test_dispatcher: Dispatcher) -> AsyncGenerator[TestClient, None]:
    """Create a test client for aiogram testing.

    This fixture provides a TestClient from aiogram-test-framework
    that can be used to simulate user interactions with the bot.
    """
    # Create bot with test token
    bot = Bot(
        token=CONFIG.token,
        default=DefaultBotProperties(parse_mode="html"),
        session=AiohttpSession(),
    )

    # Create test client
    client = TestClient(bot=bot, dispatcher=test_dispatcher)

    yield client

    # Cleanup
    await client.close()
    await bot.session.close()


@pytest.fixture(autouse=True)
async def reset_redis() -> None:
    """Reset fakeredis state between tests."""
    # Import here to avoid circular imports
    from sophie_bot.services.redis import aredis

    if hasattr(aredis, "flushall"):
        await aredis.flushall()


@pytest.fixture(scope="session", autouse=True)
async def close_redis_on_shutdown() -> AsyncGenerator[None, None]:
    """Close the global fakeredis client after all tests to avoid ResourceWarning."""
    yield

    from sophie_bot.services.redis import aredis

    await aredis.aclose()
