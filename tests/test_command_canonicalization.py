from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Router
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.filters.command import CommandObject
from aiogram.types import User

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules import discover_modules, get_module_manifest
from sophie_bot.modules.help.utils.extract_info import gather_cmds_help
from sophie_bot.modules.help.utils.format_help import format_handler, group_handlers
from sophie_bot.utils.feature_flags import FEATURE_FLAGS
from tools.wiki_gen.generate_pages import ModuleWikiPage

# Semantic audit: short aliases and action prefixes are deliberately not expanded.
CANONICAL_NAMES = {
    "op_setmode": "op_set_mode",
    "op_setbeta": "op_set_beta",
    "op_resetbeta": "op_reset_beta",
    "op_killswitch": "op_kill_switch",
    "op_aistats": "op_ai_stats",
    "op_aisetquota": "op_ai_set_quota",
    "op_airesetquota": "op_ai_reset_quota",
    "op_aiprices": "op_ai_prices",
    "op_aiproviders": "op_ai_providers",
    "op_aiprovider": "op_ai_provider",
    "op_aimodels": "op_ai_models",
    "op_aimodel": "op_ai_model",
    "aiaddfilter": "ai_add_filter",
    "aimode": "ai_mode",
    "aimoderator": "ai_moderator",
    "aiautotranslate": "ai_auto_translate",
    "autotranslate": "auto_translate",
    "aireset": "ai_reset",
    "aitranslate": "ai_translate",
    "aiusage": "ai_usage",
    "enableantiflood": "enable_antiflood",
    "antifloodenable": "antiflood_enable",
    "allowusersconnect": "allow_users_connect",
    "enableall": "enable_all",
    "accepttransfer": "accept_transfer",
    "fedadmins": "fed_admins",
    "fbanlist": "fban_list",
    "exportfbans": "export_fbans",
    "newfed": "new_fed",
    "fbanstat": "fban_stat",
    "importfbans": "import_fbans",
    "fedinfo": "fed_info",
    "joinfed": "join_fed",
    "leavefed": "leave_fed",
    "fsetlog": "f_set_log",
    "setfedlog": "set_fed_log",
    "funsetlog": "f_unset_log",
    "unsetfedlog": "unset_fed_log",
    "transferfed": "transfer_fed",
    "delfilter": "del_filter",
    "editfilter": "edit_filter",
    "addfilter": "add_filter",
    "newfilter": "new_filter",
    "enablewelcome": "enable_welcome",
    "setjoinrequest": "set_join_request",
    "deljoinrequest": "del_join_request",
    "cleanservice": "clean_service",
    "cleanwelcome": "clean_welcome",
    "setwelcome": "set_welcome",
    "locktypes": "lock_types",
    "locklanguages": "lock_languages",
    "locklangs": "lock_langs",
    "locksticker": "lock_sticker",
    "unlockall": "unlock_all",
    "delnote": "del_note",
    "clearall": "clear_all",
    "notelist": "note_list",
    "pmnotes": "pm_notes",
    "privatenotes": "private_notes",
    "addnote": "add_note",
    "cleannotes": "clean_notes",
    "resetrules": "reset_rules",
    "setrules": "set_rules",
    "admincache": "admin_cache",
    "adminlist": "admin_list",
    "resetallwarns": "reset_all_warns",
    "delallwarns": "del_all_warns",
    "resetwarns": "reset_warns",
    "delwarns": "del_warns",
    "warnaction": "warn_action",
    "warnlimit": "warn_limit",
    "warnaction_each": "warn_action_each",
    "warnaction_max": "warn_action_max",
    "welcomerestrict": "welcome_restrict",
    "welcomecaptcha": "welcome_captcha",
    "enablewelcomecaptcha": "enable_welcome_captcha",
    "setwelcomesecurity": "set_welcome_security",
    "delwelcomesecurity": "del_welcome_security",
    "welcomesecurity": "welcome_security",
}


# Complete registered-literal audit, including namespace commands and compact aliases.
UNCHANGED_NAMES = {
    "admins",
    "ai",
    "ai_note_titles",
    "ai_summaries",
    "ai_summaries_pin",
    "ai_summaries_time",
    "antiflood",
    "antiflood_action",
    "antiflood_count",
    "ban",
    "cancel",
    "captcha",
    "cban",
    "clear",
    "connect",
    "cunban",
    "del",
    "delete",
    "demote",
    "disable",
    "disableable",
    "disabled",
    "disconnect",
    "enable",
    "event",
    "export",
    "fadmins",
    "fban",
    "fchats",
    "fcheck",
    "fdelete",
    "fdemote",
    "fexport",
    "filters",
    "fimport",
    "finfo",
    "fjoin",
    "fleave",
    "flood",
    "fnew",
    "fpromote",
    "frename",
    "fsub",
    "ftransfer",
    "funban",
    "funsub",
    "get",
    "help",
    "id",
    "info",
    "instance",
    "kick",
    "lang",
    "lock",
    "lockable",
    "locked",
    "locks",
    "mute",
    "notes",
    "op_banner",
    "op_buttons",
    "op_captcha",
    "op_cmds",
    "op_debug",
    "op_ff",
    "op_gallery",
    "op_regenerate_chat_summary",
    "op_stfu_gallery",
    "op_task",
    "pin",
    "privacy",
    "promote",
    "purge",
    "report",
    "research",
    "rules",
    "save",
    "saved",
    "sban",
    "scban",
    "sfban",
    "skick",
    "smute",
    "start",
    "stats",
    "stban",
    "stmute",
    "tban",
    "tmute",
    "tr",
    "translate",
    "trust",
    "tsban",
    "tsmute",
    "unban",
    "uncban",
    "unfban",
    "unlock",
    "unmute",
    "unpin",
    "untrust",
    "unwhitelist",
    "warn",
    "warn_action",
    "warn_action_each",
    "warn_action_max",
    "warns",
    "welcome",
    "whitelist",
    "whitelisted",
}


def _registered_handlers() -> Iterator[tuple[str, Router, HandlerObject, CMDFilter]]:
    registry = discover_modules(["*"])
    for module_name, module in registry.modules.items():
        manifest = get_module_manifest(module)
        if manifest.bot_router_factory is None:
            continue
        router = manifest.bot_router_factory()
        for handler_class in manifest.handlers:
            handler_class.register(router)
        for registered_router in router.chain_tail:
            for handler in registered_router.message.handlers:
                for event_filter in handler.filters:
                    if isinstance(event_filter.callback, CMDFilter):
                        yield module_name, router, handler, event_filter.callback


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [*CANONICAL_NAMES.items(), *((name, name) for name in sorted(UNCHANGED_NAMES))],
)
async def test_registered_commands_route_all_separator_spellings_to_registered_identity(
    legacy: str,
    canonical: str,
) -> None:
    command_filters = [
        command_filter
        for _module_name, _router, _handler, command_filter in _registered_handlers()
        if canonical in command_filter.cmd
    ]
    assert command_filters, f"Missing canonical registration: {canonical}"
    bot_mock = AsyncMock(spec=Bot)
    bot_mock.me.return_value = User(id=123, is_bot=True, first_name="Sophie", username="SophieBot")
    bot = cast(Bot, bot_mock)
    for command_filter in command_filters:
        for spelling in (canonical, legacy, canonical.replace("_", ""), canonical.replace("_", "-"), canonical.upper()):
            command = await command_filter.parse_command(f"/{spelling}@SophieBot unchanged_arg-with-dash", bot)
            assert command == CommandObject(
                prefix="/", command=canonical, mention="SophieBot", args="unchanged_arg-with-dash"
            )


async def test_canonical_names_reach_help_and_wiki_without_legacy_duplicates() -> None:
    feature_values = {feature: True for feature in FEATURE_FLAGS}
    seen_routers: set[str] = set()
    found: set[str] = set()
    for module_name, router, _handler, _command_filter in _registered_handlers():
        if module_name in seen_routers:
            continue
        seen_routers.add(module_name)
        helps = await gather_cmds_help(router, feature_values, {})
        for help_entry in helps:
            for canonical in set(help_entry.cmds).intersection(CANONICAL_NAMES.values()):
                found.add(canonical)
                assert len(help_entry.cmds) == len(set(help_entry.cmds))
                rendered_help = format_handler(help_entry).to_html()
                rendered_wiki = ModuleWikiPage._table_row(help_entry)[0].to_md()
                assert f"/{canonical}" in rendered_help
                assert f"/{canonical}" in rendered_wiki
                for legacy, expected in CANONICAL_NAMES.items():
                    if expected == canonical:
                        assert f"/{legacy}</code>" not in rendered_help
                        assert f"/{legacy}`" not in rendered_wiki
    # Excluded wizard handlers have no help, but ordinary renamed commands do.
    assert {
        "reset_all_warns",
        "ai_add_filter",
        "set_rules",
        "pm_notes",
        "welcome_security",
        "op_ai_stats",
        "op_set_beta",
        "op_kill_switch",
    } <= found


async def test_ai_help_preserves_operator_visibility() -> None:
    feature_values = {feature: True for feature in FEATURE_FLAGS}
    router = next(
        router for module_name, router, _handler, _command_filter in _registered_handlers() if module_name == "ai"
    )
    helps = await gather_cmds_help(router, feature_values, {})
    operator_commands = {command for handler in helps if handler.only_op for command in handler.cmds}
    assert operator_commands == {
        "op_ai_stats",
        "op_ai_prices",
        "op_ai_providers",
        "op_ai_provider",
        "op_ai_models",
        "op_ai_model",
        "op_ai_set_quota",
        "op_ai_reset_quota",
    }
    public_commands = {
        command for _title, handlers in group_handlers(helps) for handler in handlers for command in handler.cmds
    }
    assert not operator_commands.intersection(public_commands)
    assert {"ai", "ai_mode", "ai_translate", "ai_usage"} <= public_commands
