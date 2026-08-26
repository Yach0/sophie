from __future__ import annotations

import importlib

import pytest
from aiogram import Router

from sophie_bot.modules import ModuleManifest, get_module_manifest


@pytest.mark.parametrize(
    ("module_name", "expected_handler_count"),
    [
        ("disabling", 6),
        ("notes", 13),
        ("ai", 28),
        ("help", 7),
        ("privacy", 2),
        ("locks", 8),
    ],
)
def test_module_manifest_registers_handlers(module_name: str, expected_handler_count: int) -> None:
    module = importlib.import_module(f"sophie_bot.modules.{module_name}")
    manifest = get_module_manifest(module)

    assert isinstance(manifest, ModuleManifest)
    assert manifest.name == module_name
    assert len(manifest.handlers) == expected_handler_count


def test_disabling_module_has_no_pre_setup() -> None:
    module = importlib.import_module("sophie_bot.modules.disabling")
    manifest = get_module_manifest(module)

    assert manifest.pre_setup is None


@pytest.mark.parametrize("module_name", ["disabling", "notes", "help", "privacy"])
def test_manifest_only_modules_have_no_pre_setup(module_name: str) -> None:
    module = importlib.import_module(f"sophie_bot.modules.{module_name}")
    manifest = get_module_manifest(module)

    assert manifest.pre_setup is None


def test_module_manifest_handlers_expose_register() -> None:
    module = importlib.import_module("sophie_bot.modules.disabling")
    manifest = get_module_manifest(module)

    for handler in manifest.handlers:
        assert hasattr(handler, "register")
        assert callable(handler.register)


def test_filters_module_has_no_legacy_filter_actions() -> None:
    module = importlib.import_module("sophie_bot.modules.filters")
    manifest = get_module_manifest(module)

    assert not hasattr(manifest, "filter_actions")


@pytest.mark.parametrize("module_name", ["notes", "rules", "purges", "ai"])
def test_modern_action_modules_do_not_export_legacy_filter_actions(module_name: str) -> None:
    module = importlib.import_module(f"sophie_bot.modules.{module_name}")
    manifest = get_module_manifest(module)

    assert manifest.modern_actions
    assert "filter_actions" not in manifest.__dataclass_fields__


def test_ai_context_reset_registers_both_filter_sets() -> None:
    from sophie_bot.modules.ai.handlers.reset_context import AIContextReset

    router = Router(name="test-ai-reset")
    AIContextReset.register(router)

    assert len(router.message.handlers) == 2


@pytest.mark.parametrize(
    ("module_name", "handler_name"),
    [
        ("notes", "PMNotesControl"),
        ("notes", "PMNotesStatus"),
        ("ai", "OpAIStatsHandler"),
        ("ai", "OpAIPricesHandler"),
        ("help", "PMModuleHelp"),
        ("help", "SetLangLegacyHandler"),
        ("privacy", "PrivacyMenu"),
        ("privacy", "TriggerExport"),
    ],
)
def test_manifest_contains_migrated_handlers(module_name: str, handler_name: str) -> None:
    module = importlib.import_module(f"sophie_bot.modules.{module_name}")
    manifest = get_module_manifest(module)

    assert any(handler.__name__ == handler_name for handler in manifest.handlers)
