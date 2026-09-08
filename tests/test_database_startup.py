from __future__ import annotations

from asyncio import Lock
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, call

import pytest

from sophie_bot import startup


@pytest.mark.asyncio
async def test_init_database_runs_migrations_before_syncing_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = MagicMock()
    init_db = AsyncMock(side_effect=calls.init_db)
    run_migrations = AsyncMock(side_effect=calls.run_migrations)
    database = SimpleNamespace(
        database=object(),
        initialization_lock=Lock(),
        initialized=False,
    )
    redis = object()

    monkeypatch.setattr(startup, "init_db", init_db)
    monkeypatch.setattr(startup, "run_migrations", run_migrations)

    await startup.init_database(database, redis)

    assert calls.mock_calls == [
        call.init_db(database.database, skip_indexes=True),
        call.run_migrations(ANY),
        call.init_db(database.database),
    ]
    assert database.initialized is True


@pytest.mark.asyncio
async def test_architecture_rollout_gate_allows_enabled_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    is_enabled = AsyncMock(return_value=True)
    monkeypatch.setattr(startup, "is_enabled", is_enabled)
    redis = object()

    await startup.ensure_architecture_enabled(redis)

    is_enabled.assert_awaited_once_with("architecture_refactor", redis=redis)


@pytest.mark.asyncio
async def test_architecture_rollout_gate_rejects_disabled_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        startup,
        "is_enabled",
        AsyncMock(return_value=False),
    )

    with pytest.raises(RuntimeError, match="refusing to start"):
        await startup.ensure_architecture_enabled(object())
