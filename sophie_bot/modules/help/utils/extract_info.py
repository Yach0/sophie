from __future__ import annotations

import inspect
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from itertools import chain
from types import ModuleType
from typing import Any, cast

from aiogram import Router
from aiogram.dispatcher.event.handler import FilterObject
from aiogram.types import Message
from ass_tg.types.base_abc import ArgFabric
from babel.support import LazyProxy
from stfu_tg import Doc

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules import LoadedModuleRegistry, get_module_manifest
from sophie_bot.utils.feature_flags import FeatureType, FeatureValue
from sophie_bot.utils.flags import get_disableable_name
from sophie_bot.utils.logger import log

ARGS_DICT = dict[str, ArgFabric]
ARGS_COROUTINE = Callable[
    [Message | None, dict[str, Any]], Coroutine[Any, Any, ARGS_DICT]  # Args it takes  # What function returns
]


@dataclass
class HandlerHelp:
    cmds: tuple[str, ...]
    args: ARGS_DICT | None
    description: LazyProxy | str | None
    only_admin: bool
    only_op: bool
    only_pm: bool
    only_chats: bool
    alias_to_modules: list[str]
    disableable: str | None


@dataclass
class ModuleHelp:
    handlers: list[HandlerHelp]
    name: LazyProxy | str
    icon: str
    exclude_public: bool
    info: str | LazyProxy | Doc
    description: str | LazyProxy | Doc
    advertise_wiki_page: bool


def get_aliased_cmds(help_modules: Mapping[str, ModuleHelp], module_name: str) -> dict[str, list[HandlerHelp]]:
    return {
        alias_module_name: [
            command
            for command in module.handlers
            if command.alias_to_modules and module_name in command.alias_to_modules
        ]
        for alias_module_name, module in help_modules.items()
        if any(command.alias_to_modules for command in module.handlers)
        and any(command.alias_to_modules and module_name in command.alias_to_modules for command in module.handlers)
    }


def _is_private_chat_inversion(callback: object) -> bool:
    target = getattr(callback, "target", None)
    return (
        isinstance(target, FilterObject)
        and isinstance(target.callback, ChatTypeFilter)
        and target.callback.chat_types == ("private",)
    )


def get_all_cmds(help_modules: Mapping[str, ModuleHelp]) -> list[HandlerHelp]:
    return [command for module in help_modules.values() for command in module.handlers]


def get_all_cmds_raw(help_modules: Mapping[str, ModuleHelp]) -> tuple[str, ...]:
    return tuple(command for commands in get_all_cmds(help_modules) for command in commands.cmds)


async def gather_cmd_args(args: ARGS_DICT | ARGS_COROUTINE | None) -> ARGS_DICT | None:
    if not args:
        return None
    if isinstance(args, dict):
        return args
    if inspect.iscoroutinefunction(args):
        result = await args(None, {})
        return result
    raise ValueError


async def gather_cmds_help(
    router: Router,
    feature_values: Mapping[FeatureType, FeatureValue],
    disableable_commands: dict[str, HandlerHelp],
) -> list[HandlerHelp]:
    helps: list[HandlerHelp] = []

    for sub_router in router.sub_routers:
        helps.extend(await gather_cmds_help(sub_router, feature_values, disableable_commands))

    for handler in router.message.handlers:
        if not handler.filters:
            continue

        cmd_filters = [
            handler_filter for handler_filter in handler.filters if isinstance(handler_filter.callback, CMDFilter)
        ]

        if not cmd_filters:
            continue
        cmd_filter = cast(CMDFilter, cmd_filters[0].callback)
        cmds = cast(tuple[str, ...], cmd_filter.cmd)

        # Check feature flags
        feature_flag_filters = [
            handler_filter
            for handler_filter in handler.filters
            if isinstance(handler_filter.callback, FeatureFlagFilter)
        ]
        if feature_flag_filters:
            # Check if any feature flag filter would disable this handler
            skip_handler = False
            for feature_flag_event_filter in feature_flag_filters:
                ff_filter = cast(FeatureFlagFilter, feature_flag_event_filter.callback)
                feature_enabled = bool(feature_values[ff_filter.feature])
                if feature_enabled != ff_filter.enabled:
                    skip_handler = True
                    break
            if skip_handler:
                continue

        # Is admin
        only_admin = any(isinstance(f.callback, UserRestricting) for f in handler.filters)

        # Only PMs
        only_pm = any(
            isinstance(f.callback, ChatTypeFilter) and f.callback.chat_types == ("private",) for f in handler.filters
        )

        # Only chats
        only_chats = any(_is_private_chat_inversion(event_filter.callback) for event_filter in handler.filters)

        only_op = any(isinstance(f.callback, IsOP) for f in handler.filters)

        help_flags = handler.flags.get("help")

        if help_flags and help_flags.get("exclude"):
            continue

        if help_flags and help_flags.get("args"):
            args = await gather_cmd_args(help_flags["args"])
        else:
            args = await gather_cmd_args(handler.flags.get("args"))

        disableable = get_disableable_name(handler)

        cmd = HandlerHelp(
            cmds=cmds,
            args=args,
            description=help_flags.get("description", "") if help_flags else "",
            only_admin=only_admin,
            only_op=only_op,
            only_pm=only_pm,
            only_chats=only_chats,
            alias_to_modules=help_flags.get("alias_to_modules", []) if help_flags else [],
            disableable=disableable,
        )
        helps.append(cmd)

        if disableable:
            disableable_commands[disableable] = cmd

    log.debug(f"gather_cmds_help: {router.name}", cmds=list(chain.from_iterable(mhelp.cmds for mhelp in helps)))
    return helps


async def gather_module_help(
    module: ModuleType,
    feature_values: Mapping[FeatureType, FeatureValue],
    disableable_commands: dict[str, HandlerHelp],
) -> ModuleHelp | None:
    manifest = get_module_manifest(module)
    if manifest.bot_router_factory is None:
        return None

    router = manifest.bot_router_factory()
    for handler in manifest.handlers:
        handler.register(router)

    name = manifest.title or manifest.name
    log.debug(
        f"gather_module_help: {module.__name__}",
        name=name,
        emoji=manifest.emoji or "?",
        advertise_wiki_page=manifest.advertise_wiki_page,
    )
    commands = await gather_cmds_help(router, feature_values, disableable_commands)
    if not commands:
        return None
    return ModuleHelp(
        handlers=commands,
        name=name,
        icon=manifest.emoji or "?",
        exclude_public=manifest.exclude_public,
        info=manifest.info or "",
        description=manifest.description or "",
        advertise_wiki_page=manifest.advertise_wiki_page,
    )


async def build_help_catalog(
    registry: LoadedModuleRegistry,
    feature_values: Mapping[FeatureType, FeatureValue],
) -> None:
    registry.help_modules.clear()
    registry.disableable_commands.clear()
    for module_name, module in registry.modules.items():
        module_help = await gather_module_help(module, feature_values, registry.disableable_commands)
        if module_help is None:
            continue
        if existing := registry.help_modules.get(module_name):
            module_help.handlers = existing.handlers + module_help.handlers
        registry.help_modules[module_name] = module_help
