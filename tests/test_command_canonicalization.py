from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Router
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.filters.command import CommandObject
from aiogram.types import User
from babel.messages.pofile import read_po

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules import discover_modules, get_module_manifest
from sophie_bot.modules.help.utils.extract_info import gather_cmds_help
from sophie_bot.modules.help.utils.format_help import format_handler, group_handlers
from sophie_bot.modules.utils_.status_handler import StatusHandlerABC
from sophie_bot.utils.command_names import normalize_command_name
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


def test_registered_multiword_commands_are_canonical() -> None:
    names = {
        name
        for _module_name, _router, _handler, command_filter in _registered_handlers()
        for name in command_filter.cmd
        if isinstance(name, str)
    }
    assert not names.intersection(CANONICAL_NAMES), "Legacy names must route through normalization, not appear in help"
    assert names == UNCHANGED_NAMES | set(CANONICAL_NAMES.values())
    assert len({normalize_command_name(name, ignore_case=True) for name in names}) == len(names)
    for _module_name, _router, _handler, command_filter in _registered_handlers():
        assert len(command_filter.cmd) == len(set(command_filter.cmd))


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


@pytest.mark.parametrize(
    ("module_name", "handler", "command_filter"),
    [
        (module_name, handler.callback, command_filter)
        for module_name, _router, handler, command_filter in _registered_handlers()
        if isinstance(handler.callback, type) and issubclass(handler.callback, StatusHandlerABC)
    ],
)
def test_status_change_command_is_a_registered_literal(
    module_name: str, handler: type[StatusHandlerABC], command_filter: CMDFilter
) -> None:
    if handler.change_command is not None:
        assert handler.change_command in command_filter.cmd, f"{module_name}: {handler.__name__}"


@pytest.mark.parametrize("appendix", sorted(Path("docs/modules").glob("*.md")), ids=lambda path: path.stem)
def test_docs_appendices_use_canonical_command_examples(appendix: Path) -> None:
    commands = set(re.findall(r"/([a-zA-Z][a-zA-Z0-9_-]*)", appendix.read_text()))
    assert not commands.intersection(CANONICAL_NAMES), f"{appendix}: {commands.intersection(CANONICAL_NAMES)}"


def _legacy_command_examples(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w/])/([a-zA-Z][a-zA-Z0-9_-]*)", text)).intersection(CANONICAL_NAMES)


@pytest.mark.parametrize("source", sorted(Path("sophie_bot").rglob("*.py")), ids=str)
def test_runtime_command_guidance_uses_canonical_names(source: Path) -> None:
    tree = ast.parse(source.read_text())
    excluded_nodes: set[int] = set()
    # HTTP route prefixes are API contracts, not Telegram command guidance.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "APIRouter":
            excluded_nodes.update(id(keyword.value) for keyword in node.keywords if keyword.arg == "prefix")
    # This migration documents the deliberately old disabled keys that it repairs.
    if source.name == "20260715_225616_rename_legacy_disabled_cmd_keys.py":
        excluded_nodes.add(id(tree.body[0].value))
    violations = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in excluded_nodes
            and (legacy := _legacy_command_examples(node.value))
        ):
            violations.append((node.lineno, sorted(legacy)))
    assert not violations, f"{source}: {violations}"


@pytest.mark.parametrize(
    "document",
    sorted([*Path("docs").rglob("*.md"), *Path("wiki_docs").rglob("*.md"), *Path(".").glob("*.md")]),
    ids=str,
)
def test_all_documentation_command_guidance_uses_canonical_names(document: Path) -> None:
    assert not (legacy := _legacy_command_examples(document.read_text())), f"{document}: {sorted(legacy)}"


@pytest.mark.parametrize("catalog_path", sorted(Path("locales").glob("*/LC_MESSAGES/sophie.po")), ids=str)
def test_translated_command_guidance_uses_canonical_names(catalog_path: Path) -> None:
    with catalog_path.open("rb") as catalog_file:
        catalog = read_po(catalog_file, locale=catalog_path.parts[-3])
    violations = []
    for message in catalog:
        sources = message.id if isinstance(message.id, tuple) else (message.id,)
        translations = message.string if isinstance(message.string, tuple) else (message.string,)
        for text in (*sources, *translations):
            if text and (legacy := _legacy_command_examples(text)):
                violations.append((message.id, sorted(legacy)))
    assert not violations, f"{catalog_path}: {violations}"
