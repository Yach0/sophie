from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from beanie import free_fall_migration
from fakeredis import FakeAsyncRedis, FakeRedis, FakeServer
from pymongo.errors import InvalidName

from sophie_bot.config import CONFIG
from sophie_bot.db.models.migrations import MigrationState
from sophie_bot.services import db as database_service
from sophie_bot.services import migrations as migration_service
from sophie_bot.services.migrations import MigrationResources
from tests.utils.db_fixture import initialize_beanie
from tests.utils.mongo_mock import AsyncMongoMockClient
from tools import migration_helper


class ClosingMongo(AsyncMongoMockClient):
    close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        await self.aclose()


class ClosingRedis(FakeAsyncRedis):
    close_count = 0

    async def aclose(self, close_connection_pool: bool | None = None) -> None:
        self.close_count += 1
        await super().aclose(close_connection_pool=close_connection_pool)


@pytest.fixture
async def cli_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    db_init: Any,
) -> AsyncIterator[SimpleNamespace]:
    mongo = ClosingMongo()
    redis_server = FakeServer()
    name = "20260908_000000_cli_regression"
    (tmp_path / f"{name}.py").touch()
    config = CONFIG.model_copy(
        update={
            "mongo_db": "migration_cli",
            "migrations_path": str(tmp_path),
            "run_migrations_on_startup": False,
            "mongo_skip_indexes": False,
            "migration_use_transactions": False,
        }
    )
    scenario = SimpleNamespace(
        mongo=mongo,
        database=mongo[config.mongo_db],
        redis=FakeRedis(server=redis_server),
        redis_clients=[],
        migration_name=name,
        fail_migration=False,
    )

    class Forward:
        @free_fall_migration(document_models=[])
        async def apply(self, session: Any, *, resources: MigrationResources) -> None:
            if scenario.fail_migration:
                raise RuntimeError("migration failed")
            await resources.database.database["cli_probe"].insert_one({"_id": "probe", "applied": True})
            await resources.redis.set("cli_probe", "forward")

    class Backward:
        @free_fall_migration(document_models=[])
        async def revert(self, session: Any, *, resources: MigrationResources) -> None:
            await resources.database.database["cli_probe"].delete_one({"_id": "probe"})
            await resources.redis.set("cli_probe", "backward")

    module = ModuleType(f"sophie_bot.db.migrations.{name}")
    module.Forward = Forward
    module.Backward = Backward
    monkeypatch.setitem(sys.modules, module.__name__, module)

    def create_redis(_config: Any) -> ClosingRedis:
        redis = ClosingRedis(server=redis_server)
        scenario.redis_clients.append(redis)
        return redis

    monkeypatch.setattr(migration_helper, "CONFIG", config)
    monkeypatch.setattr(migration_service, "CONFIG", config)
    monkeypatch.setattr(database_service, "AsyncMongoClient", lambda *_args: mongo)
    monkeypatch.setattr(database_service, "models", [MigrationState])
    monkeypatch.setattr(migration_helper, "create_redis", create_redis)
    try:
        yield scenario
    finally:
        scenario.redis.close()
        await mongo.aclose()
        await initialize_beanie()


@pytest.mark.parametrize("command", ["up", "run", "down", "down_all", "status"])
def test_database_commands_apply_and_report_real_migration_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cli_scenario: SimpleNamespace,
    command: str,
) -> None:
    scenario = cli_scenario
    database = scenario.database
    if command in {"down", "down_all", "status"}:
        asyncio.run(database["migration_states"].insert_one({"name": scenario.migration_name}))
        asyncio.run(database["cli_probe"].insert_one({"_id": "probe", "applied": True}))
    arguments = ["migration_helper.py", command]
    if command in {"run", "down"}:
        arguments.append(scenario.migration_name)
    monkeypatch.setattr(sys, "argv", arguments)

    migration_helper.main()

    state = asyncio.run(database["migration_states"].find_one({"name": scenario.migration_name}))
    probe = asyncio.run(database["cli_probe"].find_one({"_id": "probe"}))
    if command == "status":
        status = json.loads(capsys.readouterr().out)
        assert status["applied_migrations"] == [scenario.migration_name]
        assert status["pending_migrations"] == []
        assert status["applied"] == 1
        assert state is not None and probe is not None
        assert scenario.redis.get("cli_probe") is None
    elif command in {"up", "run"}:
        assert state is not None and state["name"] == scenario.migration_name
        assert probe == {"_id": "probe", "applied": True}
        assert scenario.redis.get("cli_probe") == b"forward"
    else:
        assert state is None and probe is None
        assert scenario.redis.get("cli_probe") == b"backward"
    assert "name_1" not in asyncio.run(database["migration_states"].index_information())
    assert scenario.mongo.close_count == 1
    assert [redis.close_count for redis in scenario.redis_clients] == [1]


@pytest.mark.parametrize("failure", ["database", "redis", "initialization", "migration"])
def test_database_command_failures_close_every_acquired_client(
    monkeypatch: pytest.MonkeyPatch,
    cli_scenario: SimpleNamespace,
    failure: str,
) -> None:
    scenario = cli_scenario
    if failure == "database":
        def fail_database(_mongo: ClosingMongo, _name: str) -> None:
            raise InvalidName("invalid database name")

        monkeypatch.setattr(ClosingMongo, "__getitem__", fail_database)
    elif failure == "redis":
        def fail_redis(_config: Any) -> None:
            raise RuntimeError("Redis creation failed")

        monkeypatch.setattr(migration_helper, "create_redis", fail_redis)
    elif failure == "initialization":
        monkeypatch.setattr(database_service, "init_beanie", AsyncMock(side_effect=RuntimeError("Beanie failed")))
    else:
        scenario.fail_migration = True
    monkeypatch.setattr(sys, "argv", ["migration_helper.py", "up"])

    with pytest.raises(SystemExit) as error:
        migration_helper.main()

    assert error.value.code == 1
    assert scenario.mongo.close_count == 1
    assert [redis.close_count for redis in scenario.redis_clients] == (
        [] if failure in {"database", "redis"} else [1]
    )
    assert asyncio.run(scenario.database["migration_states"].find_one({"name": scenario.migration_name})) is None
    assert asyncio.run(scenario.database["cli_probe"].find_one({"_id": "probe"})) is None
