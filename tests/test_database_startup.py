from __future__ import annotations

from collections.abc import AsyncIterator
from functools import partial
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from beanie import Document
from bson import DBRef, ObjectId
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError

from sophie_bot import startup
from sophie_bot.config import CONFIG, Config
from sophie_bot.runtime import build_bot_runtime, build_rest_runtime, build_scheduler_runtime
from sophie_bot.services import db as database_service
from sophie_bot.services.db import DatabaseResources
from sophie_bot.services.migrations import MigrationResources
from tests.utils.mongo_mock import AsyncMongoMockClient


class IndexPolicyDocument(Document):
    value: int

    class Settings:
        name = "startup_index_policy"
        indexes: ClassVar = [IndexModel("value", unique=True)]


@pytest.fixture
async def database(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DatabaseResources]:
    mongo = AsyncMongoMockClient()
    monkeypatch.setattr(database_service, "models", [IndexPolicyDocument])
    try:
        yield DatabaseResources(mongo=cast(Any, mongo), database=cast(Any, mongo["startup_policy"]))
    finally:
        await mongo.aclose()



@pytest.mark.asyncio
async def test_init_database_repairs_documents_before_creating_unique_indexes(
    monkeypatch: pytest.MonkeyPatch,
    database: DatabaseResources,
) -> None:
    collection = database.database[IndexPolicyDocument.Settings.name]
    await collection.insert_many([{"value": 1}, {"value": 1}])

    async def repair_duplicates(resources: MigrationResources) -> None:
        duplicate = await IndexPolicyDocument.find_one(IndexPolicyDocument.value == 1)
        assert duplicate is not None
        await duplicate.delete()

    monkeypatch.setattr(startup, "run_migrations", repair_duplicates)
    config = Config(_env_file=None, run_migrations_on_startup=True, mongo_skip_indexes=False)
    async with FakeAsyncRedis() as redis:
        await startup.init_database(database, redis, config=config)

    assert await collection.count_documents({"value": 1}) == 1
    with pytest.raises(DuplicateKeyError):
        await collection.insert_one({"value": 1})
    assert database.initialized


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["bot", "rest", "scheduler"])
async def test_runtime_config_controls_indexes_on_borrowed_database(
    monkeypatch: pytest.MonkeyPatch,
    database: DatabaseResources,
    mode: str,
) -> None:
    collection = database.database[IndexPolicyDocument.Settings.name]
    await collection.create_index("obsolete", name="obsolete")
    await collection.insert_one({"value": 1})
    monkeypatch.setattr(CONFIG, "mongo_skip_indexes", True)
    monkeypatch.setattr(CONFIG, "mongo_allow_index_dropping", False)
    monkeypatch.setattr(startup, "ensure_bot_in_db", AsyncMock())
    monkeypatch.setattr("sophie_bot.runtime.create_scheduler", lambda _config: AsyncIOScheduler())
    config = Config(
        _env_file=None,
        modules_load=[],
        run_migrations_on_startup=False,
        mongo_skip_indexes=False,
        mongo_allow_index_dropping=True,
    )
    builders = {
        "bot": (build_bot_runtime, startup.initialize_bot_mode),
        "rest": (partial(build_rest_runtime, FastAPI()), startup.initialize_rest_mode),
        "scheduler": (build_scheduler_runtime, startup.initialize_scheduler_mode),
    }
    build_runtime, initialize = builders[mode]
    async with build_runtime(config=config, database=database) as runtime:
        await initialize(runtime)

    assert "obsolete" not in await collection.index_information()
    with pytest.raises(DuplicateKeyError):
        await collection.insert_one({"value": 1})


@pytest.mark.asyncio
async def test_startup_can_skip_migrations_and_index_synchronization(
    monkeypatch: pytest.MonkeyPatch,
    database: DatabaseResources,
) -> None:
    collection = database.database[IndexPolicyDocument.Settings.name]
    await collection.create_index("obsolete", name="obsolete")
    await collection.insert_many([{"value": 1}, {"value": 1}])
    monkeypatch.setattr(CONFIG, "run_migrations_on_startup", True)
    monkeypatch.setattr(CONFIG, "mongo_skip_indexes", False)

    async def remove_documents(resources: MigrationResources) -> None:
        await resources.database.database[IndexPolicyDocument.Settings.name].delete_many({})

    monkeypatch.setattr(startup, "run_migrations", remove_documents)
    config = Config(
        _env_file=None,
        run_migrations_on_startup=False,
        mongo_skip_indexes=True,
        mongo_allow_index_dropping=True,
    )
    async with FakeAsyncRedis() as redis:
        await startup.init_database(database, redis, config=config)

    assert await collection.count_documents({"value": 1}) == 2
    assert "obsolete" in await collection.index_information()


@pytest.mark.asyncio
@pytest.mark.parametrize("skip_indexes", [False, True])
async def test_ws_unique_index_is_required_after_migrations(
    database: DatabaseResources, skip_indexes: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check_bootstrap_index(resources: MigrationResources) -> None:
        indexes = await resources.database.database["ws_users"].index_information()
        assert "ws_user_group_unique" not in indexes

    migrations = AsyncMock(side_effect=check_bootstrap_index)
    monkeypatch.setattr(startup, "run_migrations", migrations)
    config = Config(_env_file=None, run_migrations_on_startup=True, mongo_skip_indexes=skip_indexes)
    async with FakeAsyncRedis() as redis:
        await startup.init_database(database, redis, config=config)
    migrations.assert_awaited_once()
    collection = database.database["ws_users"]
    row = {"user": DBRef("chats", ObjectId()), "group": DBRef("chats", ObjectId())}
    await collection.insert_one(dict(row))
    with pytest.raises(DuplicateKeyError):
        await collection.insert_one(dict(row))


@pytest.mark.asyncio
@pytest.mark.filterwarnings(r"ignore:the \(type, exc, tb\) signature of throw:DeprecationWarning")
async def test_ws_duplicate_production_rows_fail_closed_without_data_loss(database: DatabaseResources) -> None:
    collection = database.database["ws_users"]
    pair = {"user": DBRef("chats", ObjectId()), "group": DBRef("chats", ObjectId())}
    rows = [{**pair, "transition": "completing"}, {**pair, "transition": "expiring"}]
    await collection.insert_many(rows)
    config = Config(_env_file=None, run_migrations_on_startup=False, mongo_skip_indexes=True)
    async with FakeAsyncRedis() as redis:
        with pytest.raises(RuntimeError, match="No rows were removed"):
            await startup.init_database(database, redis, config=config)
    assert not database.initialized
    assert await collection.count_documents({}) == 2
    for row in rows:
        assert await collection.find_one({"_id": row["_id"]}) == row
