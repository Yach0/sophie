import inspect
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from itertools import chain
from types import ModuleType
from typing import Any, cast

from aiogram import Router
from aiogram.filters.logic import _InvertFilter
from aiogram.types import Message
from ass_tg.types.base_abc import ArgFabric
from babel.support import LazyProxy
from stfu_tg import Doc

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules import get_module_manifest
from sophie_bot.utils.feature_flags import is_enabled
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


HELP_MODULES: OrderedDict[str, ModuleHelp] = OrderedDict()

# Keyed by the canonical disable-able name; the keys are the keyspace persisted in DisablingModel.cmds.
DISABLEABLE_CMDS: dict[str, HandlerHelp] = {}


def get_aliased_cmds(module_name) -> dict[str, list[HandlerHelp]]:
    return {
        mod_name: [cmd for cmd in module.handlers if cmd.alias_to_modules and module_name in cmd.alias_to_modules]
        for mod_name, module in HELP_MODULES.items()
        if any(cmd.alias_to_modules for cmd in module.handlers)
        and any(cmd.alias_to_modules and module_name in cmd.alias_to_modules for cmd in module.handlers)
    }


def get_all_cmds() -> list[HandlerHelp]:
    return [cmd for module in HELP_MODULES.values() for cmd in module.handlers]


def get_all_cmds_raw() -> tuple[str, ...]:
    return tuple(cmd for cmds in get_all_cmds() for cmd in cmds.cmds)


async def gather_cmd_args(args: ARGS_DICT | ARGS_COROUTINE | None) -> ARGS_DICT | None:
    if not args:
        return None
    if isinstance(args, dict):
        return cast(ARGS_DICT, args)
    if inspect.iscoroutinefunction(args):
        result = await args(None, {})
        return cast(ARGS_DICT, result)
    raise ValueError


async def gather_cmds_help(router: Router) -> list[HandlerHelp]:
    helps: list[HandlerHelp] = []

    for sub_router in router.sub_routers:
        helps.extend(await gather_cmds_help(sub_router))

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
                feature_enabled = await is_enabled(ff_filter.feature)
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
        only_chats = any(
            (
                isinstance(f.callback, _InvertFilter)
                and isinstance(f.callback.target.callback, ChatTypeFilter)
                and f.callback.target.callback.chat_types == ("private",)
            )
            for f in handler.filters
        )

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
            DISABLEABLE_CMDS[disableable] = cmd

    log.debug(f"gather_cmds_help: {router.name}", cmds=list(chain.from_iterable(mhelp.cmds for mhelp in helps)))
    return helps


async def gather_module_help(module: ModuleType) -> ModuleHelp | None:
    manifest = get_module_manifest(module)
    if manifest.bot_router is None:
        return None

    name = cast(LazyProxy | str, manifest.title or manifest.name)
    emoji = manifest.emoji or "?"
    exclude_public = manifest.exclude_public
    info = manifest.info
    description = manifest.description
    advertise_wiki_page = manifest.advertise_wiki_page

    log.debug(f"gather_module_help: {module.__name__}", name=name, emoji=emoji, advertise_wiki_page=advertise_wiki_page)

    if cmds := await gather_cmds_help(manifest.bot_router):
        return ModuleHelp(
            handlers=cmds,
            name=name,
            icon=emoji,
            exclude_public=exclude_public,
            info=info or "",
            description=description or "",
            advertise_wiki_page=advertise_wiki_page,
        )
    return None
