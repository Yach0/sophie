from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from sophie_bot import startup


@pytest.mark.asyncio
async def test_init_database_runs_migrations_before_syncing_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = MagicMock()
    init_db = AsyncMock(side_effect=calls.init_db)
    run_migrations = AsyncMock(side_effect=calls.run_migrations)

    monkeypatch.setattr(startup, "init_db", init_db)
    monkeypatch.setattr(startup, "run_migrations", run_migrations)

    await startup.init_database()

    assert calls.mock_calls == [
        call.init_db(skip_indexes=True),
        call.run_migrations(),
        call.init_db(),
    ]
