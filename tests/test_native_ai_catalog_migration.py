from __future__ import annotations

import importlib
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from sophie_bot.services.db import DatabaseResources
from sophie_bot.services.migrations import MigrationResources, _bind_migration_resources
from tests.utils.mongo_mock import AsyncMongoMockClient

migration = importlib.import_module("sophie_bot.db.migrations.20261126_142335_seed_native_ai_services")


@pytest.fixture
async def resources(test_redis: FakeAsyncRedis) -> AsyncIterator[MigrationResources]:
    mongo = AsyncMongoMockClient()
    yield MigrationResources(
        database=DatabaseResources(mongo=mongo, database=mongo["native_catalog_migration"]), redis=test_redis
    )
    await mongo.aclose()


async def test_native_catalog_seed_is_idempotent_and_reversible(resources: MigrationResources) -> None:
    forward = _bind_migration_resources(migration.Forward.migrate, resources)
    backward = _bind_migration_resources(migration.Backward.migrate, resources)
    await forward.run(session=None)
    await forward.run(session=None)
    models = resources.database.database["ai_catalog_model"]
    assert await models.count_documents({}) == 4
    mistral = await models.find_one({"name": "mistral/voxtral-mini-latest"})
    assert mistral["roles"] == [{"mode": "support", "purpose": "transcription"}]
    moderation = await models.find_one({"name": "mistral/mistral-moderation-latest"})
    assert moderation["roles"] == [
        {"mode": "support", "purpose": "moderation"},
        {"mode": "moderation", "purpose": "moderation"},
        {"mode": "entertainment", "purpose": "moderation"},
    ]
    alternate = await models.find_one({"name": "openai/omni-moderation-latest"})
    assert alternate["roles"] == [
        {"mode": "support", "purpose": "moderation", "priority": 10},
        {"mode": "moderation", "purpose": "moderation", "priority": 10},
        {"mode": "entertainment", "purpose": "moderation", "priority": 10},
    ]
    await backward.run(session=None)
    await backward.run(session=None)
    assert await models.count_documents({}) == 0
    assert await resources.database.database["ai_catalog_provider"].count_documents({}) == 0


async def test_seed_preserves_operator_keys_models_and_global_moderation_choice(resources: MigrationResources) -> None:
    database = resources.database.database
    providers = database["ai_catalog_provider"]
    original_provider = {"name": "mistral", "kind": "moderation", "api_key": "offline", "enabled": False}
    await providers.insert_one(original_provider)
    original_model = {"name": "mistral/voxtral-mini-latest", "provider": "mistral", "enabled": False, "roles": []}
    await database["ai_catalog_model"].insert_one(original_model)
    await database["feature_flag_overrides"].insert_one(
        {"feature": "ai_moderation_provider", "chat_tid": None, "value": "openai"}
    )
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    assert await providers.find_one({"name": "mistral"}) == original_provider
    assert await database["ai_catalog_model"].find_one({"name": original_model["name"]}) == original_model
    moderation = await database["ai_catalog_model"].find_one({"name": "openai/omni-moderation-latest"})
    assert len(moderation["roles"]) == 3
    await _bind_migration_resources(migration.Backward.migrate, resources).run(session=None)
    assert await providers.find_one({"name": "mistral"}) == original_provider
    assert await database["ai_catalog_model"].find_one({"name": original_model["name"]}) == original_model


async def test_seed_rollback_keeps_operator_changes(resources: MigrationResources) -> None:
    await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    database = resources.database.database
    await database["ai_catalog_provider"].update_one({"name": "openai"}, {"$set": {"api_key": "new-offline-key"}})
    await database["ai_catalog_model"].update_one(
        {"name": "openai/gpt-transcribe"}, {"$set": {"api_name": "operator-choice"}}
    )
    await database["ai_catalog_model"].update_one(
        {"name": "mistral/voxtral-mini-latest"}, {"$set": {"extra_params": {"custom": True}}}
    )
    await _bind_migration_resources(migration.Backward.migrate, resources).run(session=None)
    assert await database["ai_catalog_provider"].find_one({"name": "openai"}) is not None
    assert await database["ai_catalog_model"].find_one({"name": "openai/gpt-transcribe"}) is not None
    assert await database["ai_catalog_model"].find_one({"name": "mistral/voxtral-mini-latest"}) is not None


@pytest.mark.parametrize("direction", ["Forward", "Backward"])
async def test_catalog_migration_leaves_redis_version_for_startup(
    direction: str, resources: MigrationResources
) -> None:
    if direction == "Backward":
        await _bind_migration_resources(migration.Forward.migrate, resources).run(session=None)
    await resources.redis.set("sophie:ai:catalog_version", "17")
    controller = getattr(migration, direction).migrate
    await _bind_migration_resources(controller, resources).run(session=None)
    assert await resources.redis.get("sophie:ai:catalog_version") == b"17"
