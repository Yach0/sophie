"""Deterministic HTTP and Mongo coverage for authoritative context-size backfill."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Callable
from types import ModuleType
from typing import Any, cast

import httpx2
import pytest

from sophie_bot.services.db import DatabaseResources, get_collection
from sophie_bot.services.migrations import MigrationResources, _bind_migration_resources
from tests.utils.mongo_mock import AsyncMongoMockClient

_MIGRATION_MODULE = "sophie_bot.db.migrations.20261009_221713_add_ai_model_context_sizes"


@pytest.fixture
async def catalog_resources(test_redis: Any) -> AsyncIterator[MigrationResources]:
    """Use an isolated in-memory database, never the configured project database."""
    client = AsyncMongoMockClient()
    yield MigrationResources(
        database=DatabaseResources(mongo=cast(Any, client), database=cast(Any, client["ai_context_size_migration"])),
        redis=test_redis,
    )
    await client.aclose()


def _migration() -> ModuleType:
    return importlib.import_module(_MIGRATION_MODULE)


def _mock_openrouter(
    monkeypatch: pytest.MonkeyPatch,
    migration: ModuleType,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> list[httpx2.Request]:
    requests: list[httpx2.Request] = []
    client_type = httpx2.AsyncClient

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert str(request.url) == "https://openrouter.ai/api/v1/models"
        assert request.method == "GET"
        assert "authorization" not in request.headers
        return handler(request)

    def client_factory(**kwargs: Any) -> httpx2.AsyncClient:
        return client_type(transport=httpx2.MockTransport(handle), **kwargs)

    monkeypatch.setattr(migration.httpx2, "AsyncClient", client_factory)
    return requests


async def _run(controller: Any, resources: MigrationResources) -> None:
    await _bind_migration_resources(controller, resources).run(None)


async def _documents(resources: MigrationResources) -> dict[str, dict[str, Any]]:
    models = get_collection(resources.database.database, "ai_catalog_model")
    return {document["name"]: document async for document in models.find({})}


async def test_forward_matches_exact_names_and_api_names_once_per_run_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    catalog_resources: MigrationResources,
    capsys: pytest.CaptureFixture[str],
) -> None:
    migration = _migration()
    models = get_collection(catalog_resources.database.database, "ai_catalog_model")
    await models.insert_many(
        [
            {"name": "vendor/canonical"},
            {"name": "vendor/nullable", "context_window_tokens": None},
            {"name": "local/alias", "api_name": "vendor/remote"},
            {"name": "vendor/name-and-api", "api_name": "vendor/actual"},
            {"name": "vendor/configured", "context_window_tokens": 777},
            {"name": "vendor/unavailable", "api_name": "vendor/unpublished", "extra_params": {"key": "secret-key"}},
            {"name": "CANONICAL"},
        ]
    )
    payload = {
        "data": [
            {"id": "vendor/canonical", "context_length": 8192},
            {"id": "vendor/nullable", "context_length": 16384},
            {"id": "vendor/remote", "context_length": 32768},
            {"id": "vendor/name-and-api", "context_length": 65536},
            {"id": "vendor/actual", "context_length": 4096},
            {"id": "vendor/configured", "context_length": 131072},
        ]
    }
    requests = _mock_openrouter(monkeypatch, migration, lambda _request: httpx2.Response(200, json=payload))

    await _run(migration.Forward.migrate, catalog_resources)

    stored = await _documents(catalog_resources)
    expected = {
        "vendor/canonical": (8192, "vendor/canonical", False),
        "vendor/nullable": (16384, "vendor/nullable", True),
        "local/alias": (32768, "vendor/remote", False),
        "vendor/name-and-api": (4096, "vendor/actual", False),
    }
    for name, (capacity, source_id, was_null) in expected.items():
        assert stored[name]["context_window_tokens"] == capacity
        assert stored[name][migration._PROVENANCE_FIELD] == {
            "context_window_tokens": capacity,
            "openrouter_id": source_id,
            "was_null": was_null,
        }
    assert stored["vendor/configured"]["context_window_tokens"] == 777
    assert migration._PROVENANCE_FIELD not in stored["vendor/configured"]
    assert "context_window_tokens" not in stored["vendor/unavailable"]
    assert "context_window_tokens" not in stored["CANONICAL"]
    assert len(requests) == 1
    report = capsys.readouterr().out
    assert "vendor/unavailable" in report
    assert "vendor/unpublished" in report
    assert "CANONICAL" in report
    assert "secret-key" not in report

    # Re-fetching a changed remote catalog must not overwrite previously added values or their provenance.
    payload["data"][0]["context_length"] = 262144
    await _run(migration.Forward.migrate, catalog_resources)

    assert await _documents(catalog_resources) == stored
    assert len(requests) == 2


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"data": {}},
        {"data": []},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, None]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "", "context_length": 8192}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": 123, "context_length": 8192}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/missing"}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/invalid", "context_length": None}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/invalid", "context_length": True}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/invalid", "context_length": 0}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/invalid", "context_length": -1}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/invalid", "context_length": 8192.0}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/invalid", "context_length": "8192"}]},
        {"data": [{"id": "vendor/valid", "context_length": 8192}, {"id": "vendor/valid", "context_length": 16384}]},
    ],
)
async def test_invalid_complete_response_aborts_before_any_catalog_write(
    monkeypatch: pytest.MonkeyPatch,
    catalog_resources: MigrationResources,
    payload: object,
) -> None:
    migration = _migration()
    models = get_collection(catalog_resources.database.database, "ai_catalog_model")
    await models.insert_many(
        [
            {"name": "vendor/valid"},
            {"name": "vendor/invalid", "context_window_tokens": None},
            {"name": "vendor/configured", "context_window_tokens": 777},
        ]
    )
    before = await _documents(catalog_resources)
    requests = _mock_openrouter(monkeypatch, migration, lambda _request: httpx2.Response(200, json=payload))

    with pytest.raises((TypeError, ValueError)):
        await _run(migration.Forward.migrate, catalog_resources)

    assert await _documents(catalog_resources) == before
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["network", "status", "json"])
async def test_http_failures_abort_without_partial_writes(
    monkeypatch: pytest.MonkeyPatch,
    catalog_resources: MigrationResources,
    failure: str,
) -> None:
    migration = _migration()
    models = get_collection(catalog_resources.database.database, "ai_catalog_model")
    await models.insert_one({"name": "vendor/model"})
    before = await _documents(catalog_resources)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "network":
            raise httpx2.ReadTimeout("public metadata timed out", request=request)
        if failure == "status":
            return httpx2.Response(503, json={"data": [{"id": "vendor/model", "context_length": 8192}]})
        return httpx2.Response(200, content="not JSON")

    requests = _mock_openrouter(monkeypatch, migration, handler)
    expected_error = ValueError if failure == "json" else RuntimeError

    with pytest.raises(expected_error):
        await _run(migration.Forward.migrate, catalog_resources)

    assert await _documents(catalog_resources) == before
    assert len(requests) == 1


async def test_backward_restores_missing_and_null_values_preserves_edits_and_cleans_provenance(
    monkeypatch: pytest.MonkeyPatch,
    catalog_resources: MigrationResources,
) -> None:
    migration = _migration()
    models = get_collection(catalog_resources.database.database, "ai_catalog_model")
    await models.insert_many(
        [
            {"name": "vendor/absent"},
            {"name": "vendor/null", "context_window_tokens": None},
            {"name": "vendor/edited"},
            {"name": "vendor/removed"},
            {"name": "vendor/nulled"},
            {"name": "vendor/configured", "context_window_tokens": 777},
            {"name": "vendor/unknown"},
        ]
    )
    payload = {
        "data": [
            {"id": name, "context_length": 8192}
            for name in (
                "vendor/absent",
                "vendor/null",
                "vendor/edited",
                "vendor/removed",
                "vendor/nulled",
                "vendor/configured",
            )
        ]
    }
    requests = _mock_openrouter(monkeypatch, migration, lambda _request: httpx2.Response(200, json=payload))
    await _run(migration.Forward.migrate, catalog_resources)
    await models.update_one({"name": "vendor/edited"}, {"$set": {"context_window_tokens": 123456}})
    await models.update_one({"name": "vendor/removed"}, {"$unset": {"context_window_tokens": ""}})
    await models.update_one({"name": "vendor/nulled"}, {"$set": {"context_window_tokens": None}})
    edited = await _documents(catalog_resources)

    # Running forward again must not replace an operator's removal with a new migrated value.
    await _run(migration.Forward.migrate, catalog_resources)
    assert await _documents(catalog_resources) == edited
    await _run(migration.Backward.migrate, catalog_resources)

    stored = await _documents(catalog_resources)
    assert "context_window_tokens" not in stored["vendor/absent"]
    assert stored["vendor/null"]["context_window_tokens"] is None
    assert stored["vendor/edited"]["context_window_tokens"] == 123456
    assert "context_window_tokens" not in stored["vendor/removed"]
    assert stored["vendor/nulled"]["context_window_tokens"] is None
    assert stored["vendor/configured"]["context_window_tokens"] == 777
    assert "context_window_tokens" not in stored["vendor/unknown"]
    assert await models.count_documents({migration._PROVENANCE_FIELD: {"$exists": True}}) == 0

    await _run(migration.Backward.migrate, catalog_resources)

    assert await _documents(catalog_resources) == stored
    assert len(requests) == 2  # Rollback never fetches HTTP metadata.
