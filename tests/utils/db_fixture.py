"""Shared database fixtures for Sophie Bot tests.

Provides a single source of truth for mocked MongoDB + Beanie initialization,
eliminating the duplication across conftest.py files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from beanie import init_beanie

from sophie_bot.config import CONFIG
from sophie_bot.db.models import models
from tests.utils.mongo_mock import AsyncMongoMockClient

if TYPE_CHECKING:
    from unittest.mock import _patch


def create_mock_mongo() -> AsyncMongoMockClient:
    """Create and return a new AsyncMongoMockClient instance.

    Returns:
        A fresh mock MongoDB client wrapping mongomock.
    """
    return AsyncMongoMockClient()


def patch_pymongo(mock_client: AsyncMongoMockClient) -> _patch[Any]:
    """Create a unittest.mock patcher that replaces pymongo.AsyncMongoClient.

    Use this for module-level patching that must happen before model imports.
    The caller is responsible for calling ``patcher.start()`` and ``patcher.stop()``.

    Args:
        mock_client: The mock client to substitute in.

    Returns:
        A patcher object (not yet started).
    """
    return patch("pymongo.AsyncMongoClient", return_value=mock_client)


async def initialize_beanie(mock_client: AsyncMongoMockClient) -> Any:
    """Initialize Beanie with all document models against the given mock client.

    Args:
        mock_client: The mock MongoDB client to use.

    Returns:
        The database instance Beanie was initialized with.
    """
    database = mock_client[CONFIG.mongo_db]

    await init_beanie(
        database=database,
        document_models=models,
        allow_index_dropping=True,
        skip_indexes=True,
    )

    return database


async def cleanup_beanie() -> None:
    """Drop all documents from every registered model collection.

    Call this during fixture teardown to ensure a clean state.
    """
    for model in models:
        await model.delete_all()
