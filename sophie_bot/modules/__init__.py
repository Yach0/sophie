from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol

from aiogram import Dispatcher, Router
from fastapi import APIRouter, FastAPI

from sophie_bot.utils.logger import log

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from stfu_tg import Doc

    from sophie_bot.modules.help.utils.extract_info import HandlerHelp, ModuleHelp
    from sophie_bot.modules.utils_.action_config_wizard import ActionWizardSpec
    from sophie_bot.modules.utils_.legacy_buttons import LegacyButtonAction
    from sophie_bot.services.application import ApplicationServices
    from sophie_bot.shared.actions import ActionDefinition, ModernActionABC
    from sophie_bot.utils.handlers import SophieBaseHandler
    from sophie_bot.utils.i18n import LazyProxy


class ModuleStatsHook(Protocol):
    async def __call__(self, *, services: ApplicationServices) -> object: ...


class ExportHook(Protocol):
    async def __call__(self, chat_iid: Any, *, services: ApplicationServices) -> dict[str, Any] | None: ...


BotRouterFactory = Callable[[], Router]
ApiRouterFactory = Callable[[], APIRouter]
InitializeHook = Callable[["ApplicationServices"], Awaitable[None]]
BotSetupHook = Callable[[Router, "ApplicationServices"], Awaitable[None]]
SchedulerSetupHook = Callable[["AsyncIOScheduler", "ApplicationServices"], None]
ActionWizardBuilder = Callable[[], Mapping[str, "ActionWizardSpec"]]


@dataclass(slots=True)
class LoadedModuleRegistry:
    modules: dict[str, ModuleType] = field(default_factory=dict)
    actions: dict[str, ActionDefinition[Any]] = field(default_factory=dict)
    action_handlers: dict[str, ModernActionABC[Any]] = field(default_factory=dict)
    help_modules: OrderedDict[str, ModuleHelp] = field(default_factory=OrderedDict)
    disableable_commands: dict[str, HandlerHelp] = field(default_factory=dict)
    export_hooks: list[ExportHook] = field(default_factory=list)
    legacy_buttons: dict[str, str] = field(default_factory=dict)
    action_wizards: dict[str, ActionWizardSpec] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    name: str
    title: LazyProxy | str | None = None
    emoji: str | None = None
    description: LazyProxy | str | Doc | None = None
    info: LazyProxy | str | Doc | None = None
    bot_router_factory: BotRouterFactory | None = None
    api_router_factory: ApiRouterFactory | None = None
    handlers: Sequence[type[SophieBaseHandler]] = ()
    initialize: InitializeHook | None = None
    setup_bot: BotSetupHook | None = None
    setup_scheduler: SchedulerSetupHook | None = None
    legacy_buttons: tuple[LegacyButtonAction, ...] = ()
    build_action_wizards: ActionWizardBuilder | None = None
    advertise_wiki_page: bool = False
    exclude_public: bool = False
    stats: ModuleStatsHook | None = None
    export: ExportHook | None = None
    modern_actions: Sequence[type[ModernActionABC[Any]]] = ()


MODULES = [
    "troubleshooters",
    "rest",
    "op",
    "error",
    "users",
    "notes",
    "help",
    "federations",
    "communities",
    "privacy",
    "disabling",
    "rules",
    "promotes",
    "greetings",
    "welcomesecurity",
    "purges",
    "warns",
    "restrictions",
    "whitelist",
    "reports",
    "pins",
    "ai",
    "filters",
    "antiflood",
    "language",
    "connections",
    "locks",
    "logging",
]


def get_module_manifest(module: ModuleType) -> ModuleManifest:
    manifest = getattr(module, "module_manifest", None)
    if isinstance(manifest, ModuleManifest):
        return manifest
    raise RuntimeError(f"Module {module.__name__} must export module_manifest")


def get_loaded_module_manifest(module_name: str, module: ModuleType) -> ModuleManifest:
    manifest = get_module_manifest(module)
    if manifest.name != module_name:
        raise RuntimeError(
            f"Module {module.__name__} manifest name {manifest.name!r} must match configured name {module_name!r}"
        )
    return manifest


def discover_modules(to_load: Sequence[str], to_not_load: Sequence[str] = ()) -> LoadedModuleRegistry:
    """Import selected modules and build immutable runtime catalogs without I/O."""
    selected = MODULES if "*" in to_load else to_load
    registry = LoadedModuleRegistry()
    for module_name in (name for name in MODULES if name in selected and name not in to_not_load):
        module = import_module(f"sophie_bot.modules.{module_name}")
        manifest = get_loaded_module_manifest(module_name, module)
        registry.modules[manifest.name] = module
        if manifest.export is not None:
            registry.export_hooks.append(manifest.export)
        registry.legacy_buttons.update({action.action: action.payload_prefix for action in manifest.legacy_buttons})
        for action_type in manifest.modern_actions:
            handler = action_type()
            definition = handler.definition
            registry.action_handlers[definition.name] = handler
            registry.actions[definition.name] = definition
    log.info("Discovered modules", modules=list(registry.modules))
    return registry


async def initialize_modules(services: ApplicationServices) -> None:
    for module in services.modules.modules.values():
        initialize = get_module_manifest(module).initialize
        if initialize is not None:
            await initialize(services)


async def assemble_bot_modules(dispatcher: Dispatcher, services: ApplicationServices) -> None:
    services.modules.action_wizards.clear()
    for module in services.modules.modules.values():
        manifest = get_module_manifest(module)
        if manifest.bot_router_factory is None:
            continue
        router = manifest.bot_router_factory()
        for handler in manifest.handlers:
            handler.register(router)
        if manifest.setup_bot is not None:
            await manifest.setup_bot(router, services)
        dispatcher.include_router(router)
        if manifest.build_action_wizards is not None:
            services.modules.action_wizards.update(manifest.build_action_wizards())


def assemble_api_modules(app: FastAPI, registry: LoadedModuleRegistry) -> None:
    if getattr(app.state, "module_routes_assembled", False):
        return
    for module in registry.modules.values():
        factory = get_module_manifest(module).api_router_factory
        if factory is not None:
            template = factory()
            parent = APIRouter()
            parent.include_router(template)
            app.include_router(parent)
    app.state.module_routes_assembled = True


def track_scheduler_callback(
    callback: Callable[[], Awaitable[object]],
    services: ApplicationServices,
) -> Callable[[], Awaitable[object]]:
    """Track a running RAM job so service teardown can await its cancellation."""

    async def run() -> object:

        task = asyncio.current_task()
        if task is not None:
            services.background_tasks.add(task)
        try:
            return await callback()
        finally:
            if task is not None:
                services.background_tasks.discard(task)

    return run


def register_module_jobs(scheduler: AsyncIOScheduler, services: ApplicationServices) -> None:
    for module in services.modules.modules.values():
        setup_scheduler = get_module_manifest(module).setup_scheduler
        if setup_scheduler is not None:
            setup_scheduler(scheduler, services)
