"""Shared database fixtures for Sophie Bot tests.

Provides a single source of truth for mocked MongoDB + Beanie initialization,
eliminating the duplication across conftest.py files.
"""

from __future__ import annotations

from typing import Any

from beanie import init_beanie

from sophie_bot.config import CONFIG
from sophie_bot.db.models import models
from tests.utils.mongo_mock import AsyncMongoMockClient

# Beanie binds collections onto model classes, so the test process shares one explicit
# mock owner and initializes it once.
MOCK_MONGO = AsyncMongoMockClient()




async def initialize_beanie(mock_client: AsyncMongoMockClient = MOCK_MONGO) -> Any:
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
