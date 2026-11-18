from __future__ import annotations

import importlib
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils import ai_catalog
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_model_plan
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.db import DatabaseResources
from sophie_bot.services.migrations import MigrationResources, _bind_migration_resources
from tests.utils.mongo_mock import AsyncMongoMockClient

migration = importlib.import_module("sophie_bot.db.migrations.20261113_225646_add_memory_writer_terra")
qualified_migration = importlib.import_module("sophie_bot.db.migrations.20261118_224519_qualify_memory_writer_model")


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


@pytest.mark.parametrize("api_fields", [{}, {"api_name": None}, {"api_name": ""}, {"api_name": "gpt-6.1-terra"}])
async def test_qualify_writer_name_is_idempotent_and_reversible(
    resources: MigrationResources, api_fields: dict[str, str | None]
) -> None:
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    collection = resources.database.database["ai_catalog_model"]
    await collection.update_one({"name": "gpt-6.1-terra"}, {"$set": api_fields})
    original = await collection.find_one({"name": "gpt-6.1-terra"})
    forward = _bind_migration_resources(qualified_migration.Forward.migrate, resources)
    backward = _bind_migration_resources(qualified_migration.Backward.migrate, resources)
    await forward.run(session=None)
    await forward.run(session=None)
    model = await collection.find_one({"name": "gpt-6.1-terra"})
    assert model and model["api_name"] == "openai/gpt-6.1-terra"
    assert model["roles"] == original["roles"]
    await backward.run(session=None)
    await backward.run(session=None)
    assert await collection.find_one({"name": "gpt-6.1-terra"}) == original


@pytest.mark.parametrize("provider", ["openrouter", "custom"])
async def test_qualify_writer_preserves_custom_configuration(resources: MigrationResources, provider: str) -> None:
    collection = resources.database.database["ai_catalog_model"]
    original = {"name": "gpt-6.1-terra", "provider": provider, "api_name": "custom/terra"}
    await collection.insert_one(original)
    await _bind_migration_resources(qualified_migration.Forward.migrate, resources).run(session=None)
    await _bind_migration_resources(qualified_migration.Backward.migrate, resources).run(session=None)
    assert await collection.find_one({"name": "gpt-6.1-terra"}) == original


async def test_qualify_writer_rollback_preserves_later_operator_change(resources: MigrationResources) -> None:
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    await _bind_migration_resources(qualified_migration.Forward.migrate, resources).run(session=None)
    collection = resources.database.database["ai_catalog_model"]
    await collection.update_one({"name": "gpt-6.1-terra"}, {"$set": {"api_name": "custom/terra"}})
    await _bind_migration_resources(qualified_migration.Backward.migrate, resources).run(session=None)
    model = await collection.find_one({"name": "gpt-6.1-terra"})
    assert model and model["api_name"] == "custom/terra"


async def test_migrated_writer_builds_real_openrouter_plan(
    test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = MigrationResources(database=test_services.db, redis=test_services.redis)
    monkeypatch.setattr(ai_catalog, "_catalog", ai_catalog.AICatalog())
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    await _bind_migration_resources(qualified_migration.Forward.migrate, resources).run(session=None)
    await ai_catalog.load_catalog(redis=resources.redis)
    plan = await get_chat_model_plan(
        AIModelPurpose.memory_writer, None, mode=AIMode.entertainment, redis=resources.redis
    )
    assert len(plan.candidates) == 1
    assert plan.candidates[0].model.model_name == "openai/gpt-6.1-terra"
    assert plan.candidates[0].model_name == "gpt-6.1-terra"
