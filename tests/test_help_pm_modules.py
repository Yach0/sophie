from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sophie_bot.modules.help.callbacks import PMHelpModule
from sophie_bot.modules.help.handlers.pm_modules import PMModuleHelp, PMModulesList
from sophie_bot.modules.help.utils.extract_info import ModuleHelp


def _module_help(name: str, icon: str = "🔧") -> ModuleHelp:
    return ModuleHelp(
        handlers=[],
        name=name,
        icon=icon,
        exclude_public=False,
        info="",
        description="",
        advertise_wiki_page=False,
    )


def _button_module_names(builder_mock: MagicMock) -> list[str]:
    """Pull the module names out of the PMHelpModule callback data of every rendered button."""
    names: list[str] = []
    for call in builder_mock.row.call_args_list:
        for button in call.args:
            callback_data = getattr(button, "callback_data", None)
            if callback_data and callback_data.startswith(PMHelpModule.__prefix__):
                names.append(PMHelpModule.unpack(callback_data).module_name)
    return names


@pytest.mark.asyncio
async def test_stale_module_button_answers_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A button for a module that dropped out of HELP_MODULES must not raise KeyError.

    Modules disappear from HELP_MODULES when every command is gated behind a disabled feature flag,
    so an open help menu can outlive its own buttons.
    """
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.HELP_MODULES", {})

    handler = PMModuleHelp.__new__(PMModuleHelp)
    handler.data = {"callback_data": PMHelpModule(module_name="locks", back_to_start=False)}
    handler.event = MagicMock()
    handler.event.answer = AsyncMock()
    handler.edit_text = AsyncMock()

    await handler.handle()

    handler.event.answer.assert_awaited_once()
    handler.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_featured_module_is_rendered_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """A featured module that already sorts alphabetically must still be moved to the bottom."""
    help_modules = {
        "ai": _module_help("AI"),
        "notes": _module_help("Notes"),
        "zzz": _module_help("Zzz"),
    }
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.HELP_MODULES", help_modules)
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.CONFIG.help_featured_module", "notes")

    builder = MagicMock()
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.InlineKeyboardBuilder", lambda: builder)

    handler = PMModulesList.__new__(PMModulesList)
    handler.data = {}
    handler.answer_rich = AsyncMock()

    await handler.handle()

    assert _button_module_names(builder) == ["ai", "zzz", "notes"]


@pytest.mark.asyncio
async def test_default_featured_module_is_rendered_last(monkeypatch: pytest.MonkeyPatch) -> None:
    help_modules = {
        "ai": _module_help("AI"),
        "notes": _module_help("Notes"),
        "zzz": _module_help("Zzz"),
    }
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.HELP_MODULES", help_modules)
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.CONFIG.help_featured_module", "ai")

    builder = MagicMock()
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.InlineKeyboardBuilder", lambda: builder)

    handler = PMModulesList.__new__(PMModulesList)
    handler.data = {}
    handler.answer_rich = AsyncMock()

    await handler.handle()

    assert _button_module_names(builder) == ["notes", "zzz", "ai"]
