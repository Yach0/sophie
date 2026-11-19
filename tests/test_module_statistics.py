from __future__ import annotations

from types import ModuleType
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules import LoadedModuleRegistry, ModuleManifest
from sophie_bot.modules.op.handlers.stats import get_system_stats
from sophie_bot.services.application import ApplicationServices


@pytest.mark.parametrize("module_names", [(), ("internal", "public")])
async def test_system_statistics_count_registry_modules_once(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    module_names: tuple[str, ...],
) -> None:
    registry = LoadedModuleRegistry()
    for module_name in module_names:
        module = ModuleType(f"sophie_bot.modules.{module_name}")
        module.module_manifest = ModuleManifest(name=module_name, exclude_public=module_name == "internal")
        registry.modules[module_name] = module
    monkeypatch.setattr(test_services, "modules", registry)
    monkeypatch.setattr(test_services.db.database, "command", AsyncMock(return_value={"storageSize": 0}))
    monkeypatch.setattr(
        "sophie_bot.modules.op.handlers.stats.get_migration_status",
        AsyncMock(return_value={"status": "unavailable"}),
    )

    rendered = (await get_system_stats(test_services)).to_html()

    assert rendered.count("Modules") == 1
    assert f"<code>{len(module_names)}</code> loaded" in rendered
    assert "Legacy modules" not in rendered
    assert "total commands registered" not in rendered
