from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters.command import CommandException

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules import communities
from sophie_bot.modules.communities.handlers.cban import CommunityBanHandler, SilentCommunityBanHandler
from sophie_bot.modules.help.callbacks import PMHelpModule
from sophie_bot.modules.help.handlers.pm_modules import PMModuleHelp
from sophie_bot.modules.help.utils.extract_info import gather_module_help
from tools.wiki_gen.generate_pages import ModuleWikiPage


def test_community_bans_have_unique_registrations_and_shared_behavior() -> None:
    manifest = communities.module_manifest
    assert manifest.bot_router_factory is not None
    router = manifest.bot_router_factory()
    for handler in manifest.handlers:
        handler.register(router)

    registrations = [
        (registration.callback, event_filter.callback)
        for registration in router.message.handlers
        for event_filter in registration.filters or []
        if isinstance(event_filter.callback, CMDFilter)
        and registration.callback in (CommunityBanHandler, SilentCommunityBanHandler)
    ]
    assert [(handler, cmd_filter.cmd) for handler, cmd_filter in registrations] == [
        (CommunityBanHandler, ("cban",)),
        (SilentCommunityBanHandler, ("scban",)),
    ]
    for (_, cmd_filter), expected_command in zip(registrations, ("cban", "scban"), strict=True):
        for command in ("cban", "scban"):
            parsed = CMDFilter.extract_command(f"!{command} 123 reason")
            if command == expected_command:
                assert cmd_filter.validate_command(parsed).command == command
            else:
                with pytest.raises(CommandException):
                    cmd_filter.validate_command(parsed)
    assert SilentCommunityBanHandler.handle is CommunityBanHandler.handle
    assert SilentCommunityBanHandler.filters()[1:] == CommunityBanHandler.filters()[1:]
    assert SilentCommunityBanHandler.handler_args.__func__ is CommunityBanHandler.handler_args.__func__


@pytest.mark.asyncio
async def test_community_ban_registrations_produce_separate_help_and_wiki_entries() -> None:
    module_help = await gather_module_help(communities, {}, {})
    assert module_help is not None
    ban_help = [handler for handler in module_help.handlers if "cban" in handler.cmds or "scban" in handler.cmds]
    assert [handler.cmds for handler in ban_help] == [("cban",), ("scban",)]
    assert all(handler.only_admin for handler in ban_help)
    assert ban_help[0].args is not None and ban_help[1].args is not None
    assert ban_help[0].args.keys() == ban_help[1].args.keys()
    assert str(ban_help[0].description) != str(ban_help[1].description)

    help_modules = {"communities": module_help}
    handler = PMModuleHelp.__new__(PMModuleHelp)
    handler.data = {
        "services": SimpleNamespace(modules=SimpleNamespace(help_modules=help_modules)),
        "callback_data": PMHelpModule(module_name="communities", back_to_start=False),
    }
    handler.answer_rich = AsyncMock()
    await handler.handle()

    assert handler.answer_rich.await_args is not None
    rendered_help = handler.answer_rich.await_args.args[0].to_html()
    help_lines = [line for line in rendered_help.splitlines() if "/cban" in line or "/scban" in line]
    assert len(help_lines) == 2
    assert all(("/cban" in line) != ("/scban" in line) for line in help_lines)

    wiki = ModuleWikiPage("communities", module_help, help_modules).page
    ban_rows = [line for line in wiki.splitlines() if "`/cban`" in line or "`/scban`" in line]
    assert len(ban_rows) == 2
    assert all(("`/cban`" in row) != ("`/scban`" in row) for row in ban_rows)
