"""Global pytest fixtures for Sophie Bot tests.

This module provides fixtures for both unit tests and e2e tests.
For e2e tests, it sets up mocked MongoDB (via mongomock) and Redis (via fakeredis)
so tests can run without external services.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

from aiogram import Bot

# Mock PyICU if not available (required by normality but needs system-level ICU libs)
if "icu" not in sys.modules:
    try:
        import icu  # noqa: F401
    except ImportError:
        sys.modules["icu"] = MagicMock()

import mistralai.client.httpclient
import mistralai.client.sdk
import pytest
from fakeredis import FakeAsyncRedis

from sophie_bot.config import CONFIG
from sophie_bot.db.cache.locale import LocaleStore
from sophie_bot.modules import LoadedModuleRegistry
from sophie_bot.modules.utils_.delayed_delete import DelayedDeletionService
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.db import DatabaseResources
from sophie_bot.utils.cached import RedisCache
from sophie_bot.utils.i18n import I18nNew
from tests.utils.db_fixture import MOCK_MONGO, cleanup_beanie, initialize_beanie

logger = logging.getLogger(__name__)

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
            logger.debug("Failed to close async client during teardown", exc_info=True)

    owner.client = None
    owner.async_client = None


mistralai.client.httpclient.close_clients = _safe_close_clients
mistralai.client.sdk.close_clients = _safe_close_clients


@pytest.fixture(scope="session", autouse=True)
def i18n_context() -> Any:
    """Provide i18n context for all tests.

    Built with the same domain and default locale as the production instance in
    sophie_bot/services/i18n.py. Without `domain="sophie"` this falls back to aiogram's
    default domain ("messages"), matches none of Sophie's catalogs, and silently yields an
    i18n with zero available locales -- so any code under test that checks
    `available_locales` sees an empty tuple and rejects every locale.
    """
    i18n = I18nNew(path="locales", domain="sophie", default_locale=CONFIG.default_locale)
    from ass_tg.i18n import gettext_ctx

    token = gettext_ctx.set(i18n)

    with i18n.context():
        yield i18n

    gettext_ctx.reset(token)


@pytest.fixture(scope="session")
async def mock_mongo() -> AsyncGenerator[Any]:
    """Expose the process-wide explicit mocked MongoDB owner."""
    yield MOCK_MONGO
    await MOCK_MONGO.aclose()


@pytest.fixture(scope="session")
async def db_init(mock_mongo: Any) -> AsyncGenerator[Any]:
    """Initialize Beanie with mocked MongoDB.

    This fixture sets up Beanie ODM with all models using the mocked MongoDB.
    """
    database = await initialize_beanie(mock_mongo)

    yield database

    await cleanup_beanie()


@pytest.fixture(scope="session")
async def test_redis() -> AsyncGenerator[FakeAsyncRedis]:
    redis = FakeAsyncRedis(
        decode_responses=False,
        single_connection_client=True,
    )
    yield redis
    await redis.aclose()


@pytest.fixture(scope="session")
async def test_services(
    db_init: Any,
    i18n_context: I18nNew,
    test_redis: FakeAsyncRedis,
) -> AsyncGenerator[ApplicationServices]:
    """Provide the explicit application boundary used by unit tests."""
    cache = RedisCache(test_redis)
    yield ApplicationServices(
        bot=MagicMock(spec=Bot),
        redis=test_redis,
        db=DatabaseResources(
            mongo=MOCK_MONGO,
            database=db_init,
            initialized=True,
        ),
        cache=cache,
        locales=LocaleStore(cache, i18n_context, CONFIG.default_locale),
        deletions=MagicMock(spec=DelayedDeletionService),
        modules=MagicMock(spec=LoadedModuleRegistry),
        background_tasks=set(),
    )


@pytest.fixture(autouse=True)
async def reset_redis(test_redis: FakeAsyncRedis) -> None:
    """Reset the explicit shared fake Redis state between tests."""
    await test_redis.flushall()
