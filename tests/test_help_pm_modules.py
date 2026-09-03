from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from stfu_tg import Buttons

from sophie_bot.modules.help.callbacks import PMHelpModule, PMHelpModules
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


def _button_module_names(handler: PMModulesList) -> list[str]:
    """Pull module names out of embedded Rich Message buttons."""
    doc = handler.answer_rich.await_args.args[0]
    button_group = next(element for element in doc if isinstance(element, Buttons))
    return [
        PMHelpModule.unpack(button.callback_data).module_name
        for row in button_group.rows
        for button in row.buttons
        if button.callback_data and button.callback_data.startswith(PMHelpModule.__prefix__)
    ]


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

    handler = PMModulesList.__new__(PMModulesList)
    handler.data = {}
    handler.answer_rich = AsyncMock()

    await handler.handle()

    assert _button_module_names(handler) == ["ai", "zzz", "notes"]


@pytest.mark.asyncio
async def test_default_featured_module_is_rendered_last(monkeypatch: pytest.MonkeyPatch) -> None:
    help_modules = {
        "ai": _module_help("AI"),
        "notes": _module_help("Notes"),
        "zzz": _module_help("Zzz"),
    }
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.HELP_MODULES", help_modules)
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.CONFIG.help_featured_module", "ai")

    handler = PMModulesList.__new__(PMModulesList)
    handler.data = {}
    handler.answer_rich = AsyncMock()
    await handler.handle()

    assert _button_module_names(handler) == ["notes", "zzz", "ai"]


@pytest.mark.asyncio
async def test_module_wiki_is_embedded_as_rich_button(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module_help("Notes")
    module.advertise_wiki_page = True
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.HELP_MODULES", {"notes": module})
    monkeypatch.setattr("sophie_bot.modules.help.handlers.pm_modules.get_aliased_cmds", lambda _: {})

    handler = PMModuleHelp.__new__(PMModuleHelp)
    handler.data = {"callback_data": PMHelpModule(module_name="notes", back_to_start=False)}
    handler.answer_rich = AsyncMock()

    await handler.handle()

    doc = handler.answer_rich.await_args.args[0]
    button_group = next(element for element in doc if isinstance(element, Buttons))
    assert button_group.rows[0].buttons[0].url == "https://sophie-wiki.orangefox.tech/modules/notes"
    markup = handler.answer_rich.await_args.kwargs["reply_markup"]
    assert [button.callback_data for row in markup.inline_keyboard for button in row] == [
        PMHelpModules(back_to_start=False).pack()
    ]
