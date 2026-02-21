"""E2E test fixtures and utilities.

This module provides fixtures specifically for end-to-end testing
using aiogram-test-framework with mocked services.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest_asyncio
from aiogram import Dispatcher
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_test_framework import TestClient
from beanie import init_beanie
from fakeredis import FakeAsyncRedis

from sophie_bot.config import CONFIG

if TYPE_CHECKING:
    pass

# Import and set up mock BEFORE any Beanie imports
from tests.utils.mongo_mock import AsyncMongoMockClient

# Create mock client early
_mock_mongo_client = AsyncMongoMockClient()

# Patch pymongo before any other imports that might use it
patch("pymongo.AsyncMongoClient", return_value=_mock_mongo_client).start()

# Now import models after patching
from sophie_bot.db.models import models  # noqa: E402
from sophie_bot.modules import load_modules  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def mock_mongo() -> AsyncGenerator[Any, None]:
    """Create a mocked MongoDB client for e2e tests.

    This fixture patches pymongo.AsyncMongoClient to use our AsyncMongoMockClient
    which wraps mongomock and provides async compatibility for Beanie 2.0.
    """
    yield _mock_mongo_client


@pytest_asyncio.fixture(scope="session")
async def db_init(mock_mongo: Any) -> AsyncGenerator[Any, None]:
    """Initialize Beanie with mocked MongoDB.

    This fixture sets up Beanie ODM with all models using the mocked MongoDB.
    """
    # Get database from mock client
    db = mock_mongo[CONFIG.mongo_db]

    # Initialize Beanie with all models
    await init_beanie(
        database=db,
        document_models=models,
        allow_index_dropping=True,
        skip_indexes=True,  # Skip indexes for faster tests
    )

    yield db

    # Cleanup: drop all collections after tests
    for model in models:
        await model.delete_all()


@pytest_asyncio.fixture(scope="session")
async def test_dispatcher(db_init: Any) -> AsyncGenerator[Dispatcher, None]:
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

    # Set up middlewares
    from ass_tg.middleware import ArgsMiddleware

    from sophie_bot.middlewares import (
        ConnectionsMiddleware,
        DisablingMiddleware,
        LocalizationMiddleware,
        SaveChatsMiddleware,
    )
    from sophie_bot.services.i18n import i18n

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
async def test_client(test_dispatcher: Dispatcher) -> AsyncGenerator[TestClient, None]:
    """Create a test client for aiogram testing.

    This fixture provides a TestClient from aiogram-test-framework
    that can be used to simulate user interactions with the bot.
    """
    from aiogram_test_framework.mock_bot import MockBot
    from aiogram_test_framework.request_capture import RequestCapture

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

    # Create test client
    client = TestClient(dispatcher=test_dispatcher, bot=mock_bot, capture=capture)

    yield client

    # Only reset captures/counters — do NOT call client.close() because it
    # disconnects the session-scoped dispatcher's router tree and emits
    # shutdown, which breaks all subsequent tests that reuse the dispatcher.
    client.reset()
