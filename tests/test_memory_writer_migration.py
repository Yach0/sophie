from __future__ import annotations

import importlib
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from sophie_bot.services.db import DatabaseResources
from sophie_bot.services.migrations import MigrationResources, _bind_migration_resources
from tests.utils.mongo_mock import AsyncMongoMockClient

migration = importlib.import_module("sophie_bot.db.migrations.20261113_225646_add_memory_writer_terra")


@pytest.fixture
async def resources(test_redis: FakeAsyncRedis) -> AsyncIterator[MigrationResources]:
    mongo = AsyncMongoMockClient()
    yield MigrationResources(
        database=DatabaseResources(mongo=mongo, database=mongo["memory_writer_migration"]), redis=test_redis
    )
    await mongo.aclose()


async def test_memory_writer_catalog_migration_is_idempotent_and_reversible(resources: MigrationResources) -> None:
    forward = _bind_migration_resources(migration.Forward.migrate, resources)
    backward = _bind_migration_resources(migration.Backward.migrate, resources)
    await forward.run(session=None)
    await forward.run(session=None)
    database = resources.database.database
    model = await database["ai_catalog_model"].find_one({"name": "gpt-6.1-terra"})
    assert model and model["provider"] == "openrouter" and model["enabled"]
    assert model["roles"] == [{"mode": "entertainment", "purpose": "memory_writer"}]
    assert await database["ai_catalog_provider"].count_documents({}) == 1
    await backward.run(session=None)
    await backward.run(session=None)
    assert await database["ai_catalog_model"].count_documents({}) == 0
    assert await database["ai_catalog_provider"].count_documents({}) == 0


async def test_migration_preserves_existing_settings_and_restores_them(resources: MigrationResources) -> None:
    database = resources.database.database
    await database["ai_catalog_provider"].insert_one(
        {"name": "openrouter", "kind": "openrouter", "api_key": "test-key"}
    )
    original = {
        "name": "gpt-6.1-terra",
        "provider": "openrouter",
        "enabled": False,
        "roles": [{"mode": "support", "purpose": "summary"}],
        "extra_params": {"temperature": 0.2},
    }
    await database["ai_catalog_model"].insert_one(original)
    forward = _bind_migration_resources(migration.Forward.migrate, resources)
    await forward.run(session=None)
    await forward.run(session=None)
    model = await database["ai_catalog_model"].find_one({"name": "gpt-6.1-terra"})
    assert not model["enabled"]
    assert len(model["roles"]) == 2
    assert model["extra_params"] == original["extra_params"]
    await _bind_migration_resources(migration.Backward.migrate, resources).run(session=None)
    assert await database["ai_catalog_model"].find_one({"name": "gpt-6.1-terra"}) == original
    assert await database["ai_catalog_provider"].count_documents({}) == 1


async def test_rollback_keeps_a_provider_configured_after_migration(resources: MigrationResources) -> None:
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    providers = resources.database.database["ai_catalog_provider"]
    await providers.update_one({"name": "openrouter"}, {"$set": {"api_key": "configured-key"}})
    await _bind_migration_resources(migration.Backward.migrate, resources).run(session=None)
    assert await providers.count_documents({"name": "openrouter"}) == 1
