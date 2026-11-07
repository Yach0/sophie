from __future__ import annotations

import importlib

import pytest
from aiogram import Router

from sophie_bot.modules import MODULES, discover_modules, get_module_manifest
from sophie_bot.modules.ai.handlers.reset_context import AIContextReset


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


def test_discovery_registers_all_manifest_actions_and_exports() -> None:
    registry = discover_modules(["*"])

    assert list(registry.modules) == MODULES
    expected_actions = {}
    expected_exports = []
    for module_name, module in registry.modules.items():
        manifest = get_module_manifest(module)
        assert manifest.name == module_name
        for action_type in manifest.modern_actions:
            expected_actions[action_type.definition.name] = action_type
        if manifest.export is not None:
            expected_exports.append(manifest.export)

    assert expected_actions
    assert expected_exports
    assert registry.actions.keys() == expected_actions.keys()
    assert registry.action_handlers.keys() == expected_actions.keys()
    for action_name, action_type in expected_actions.items():
        assert registry.actions[action_name] is action_type.definition
        assert isinstance(registry.action_handlers[action_name], action_type)
    assert registry.export_hooks == expected_exports


def test_discovery_selection_is_ordered_and_runtime_local() -> None:
    registry = discover_modules(["notes", "users", "help"], ["help"])
    other_registry = discover_modules(["users"])

    assert list(registry.modules) == ["users", "notes"]
    assert list(other_registry.modules) == ["users"]
    assert registry.actions
    assert not other_registry.actions
    assert not other_registry.action_handlers
    assert not other_registry.export_hooks
    assert not discover_modules([]).modules
