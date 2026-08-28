from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from sophie_bot import startup


@pytest.mark.asyncio
async def test_init_database_repairs_legacy_data_before_running_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = MagicMock()
    init_db = AsyncMock(side_effect=calls.init_db)
    repair_legacy_database_data = AsyncMock(side_effect=calls.repair_legacy_database_data)
    run_migrations = AsyncMock(side_effect=calls.run_migrations)

    monkeypatch.setattr(startup, "init_db", init_db)
    monkeypatch.setattr(startup, "repair_legacy_database_data", repair_legacy_database_data)
    monkeypatch.setattr(startup, "run_migrations", run_migrations)

    await startup.init_database()

    assert calls.mock_calls == [
        call.init_db(skip_indexes=True),
        call.repair_legacy_database_data(),
        call.run_migrations(),
        call.init_db(),
    ]
