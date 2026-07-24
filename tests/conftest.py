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
from unittest.mock import MagicMock, patch

# Mock PyICU if not available (required by normality but needs system-level ICU libs)
if "icu" not in sys.modules:
    try:
        import icu  # noqa: F401
    except ImportError:
        sys.modules["icu"] = MagicMock()

import mistralai.client.httpclient
import mistralai.client.sdk
import pytest

from sophie_bot.config import CONFIG
from sophie_bot.utils.i18n import I18nNew
from tests.utils.db_fixture import (
    MOCK_MONGO,
    cleanup_beanie,
    initialize_beanie,
    stop_mongo_patch,
)

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
async def mock_mongo() -> AsyncGenerator[Any, None]:
    """Expose the process-wide mocked MongoDB client.

    ``pymongo.AsyncMongoClient`` is already patched to return it by importing
    ``tests.utils.db_fixture``; ``sophie_bot.services.db.async_mongo`` still needs
    redirecting because it captured a client at import time.
    """
    with patch("sophie_bot.services.db.async_mongo", MOCK_MONGO):
        yield MOCK_MONGO

    stop_mongo_patch()
    await MOCK_MONGO.aclose()


@pytest.fixture(scope="session")
async def db_init(mock_mongo: Any) -> AsyncGenerator[Any, None]:
    """Initialize Beanie with mocked MongoDB.

    This fixture sets up Beanie ODM with all models using the mocked MongoDB.
    """
    database = await initialize_beanie(mock_mongo)

    yield database

    await cleanup_beanie()


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
