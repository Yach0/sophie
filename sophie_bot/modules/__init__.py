from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable, Iterator, Sequence
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass, field
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol

from aiogram import Dispatcher, Router

from sophie_bot.utils.logger import log

if TYPE_CHECKING:
    from fastapi import APIRouter
    from stfu_tg import Doc

    from sophie_bot.utils.handlers import SophieBaseHandler
    from sophie_bot.utils.i18n import LazyProxy


class ModuleStatsHook(Protocol):
    async def __call__(self) -> object: ...


class ExportHook(Protocol):
    async def __call__(self, chat_iid: Any) -> dict[str, Any] | None: ...


PreSetupHook = Callable[[], Awaitable[None]]
PostSetupHook = Callable[[dict[str, ModuleType]], Awaitable[None]]


@dataclass(slots=True)
class LoadedModuleRegistry:
    modules: dict[str, ModuleType] = field(default_factory=dict)
    api_routers: list[APIRouter] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    name: str
    title: LazyProxy | str | None = None
    emoji: str | None = None
    description: LazyProxy | str | Doc | None = None
    info: LazyProxy | str | Doc | None = None
    bot_router: Router | None = None
    api_router: APIRouter | None = None
    handlers: Sequence[type[SophieBaseHandler]] = ()
    pre_setup: PreSetupHook | None = None
    post_setup: PostSetupHook | None = None
    scheduler_jobs: Sequence[object] = ()
    advertise_wiki_page: bool = False
    exclude_public: bool = False
    stats: ModuleStatsHook | None = None
    export: ExportHook | None = None
    modern_actions: SequenceABC[type[Any]] = ()


_loaded_module_registry = LoadedModuleRegistry()


def create_loaded_module_registry() -> LoadedModuleRegistry:
    return LoadedModuleRegistry()


def get_loaded_module_registry() -> LoadedModuleRegistry:
    return _loaded_module_registry


def set_loaded_module_registry(registry: LoadedModuleRegistry) -> LoadedModuleRegistry:
    global _loaded_module_registry

    _loaded_module_registry = registry
    return registry


class LoadedModulesProxy:
    def _modules(self) -> dict[str, ModuleType]:
        return get_loaded_module_registry().modules

    def __getitem__(self, key: str) -> ModuleType:
        return self._modules()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._modules())

    def __len__(self) -> int:
        return len(self._modules())

    def values(self):
        return self._modules().values()

    def items(self):
        return self._modules().items()

    def keys(self):
        return self._modules().keys()

    def get(self, key: str, default: ModuleType | None = None) -> ModuleType | None:
        return self._modules().get(key, default)


class LoadedApiRoutersProxy:
    def _api_routers(self) -> list[APIRouter]:
        return get_loaded_module_registry().api_routers

    def __getitem__(self, index: int) -> APIRouter:
        return self._api_routers()[index]

    def __len__(self) -> int:
        return len(self._api_routers())

    def __iter__(self) -> Iterator[APIRouter]:
        return iter(self._api_routers())


LOADED_MODULES = LoadedModulesProxy()
LOADED_API_ROUTERS = LoadedApiRoutersProxy()

MODULES = [
    "troubleshooters",  # troubleshooters always first!
    "rest",
    "op",
    "error",
    "users",
    "notes",
    "help",
    "federations",
    "communities",  # After feds
    "privacy",
    "disabling",
    "rules",
    "promotes",
    "greetings",  # After feds
    "welcomesecurity",
    "purges",
    "warns",
    "restrictions",
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

    msg = f"Module {module.__name__} must export module_manifest"
    raise RuntimeError(msg)


def get_loaded_module_manifest(module_name: str, module: ModuleType) -> ModuleManifest:
    manifest = get_module_manifest(module)
    if manifest.name != module_name:
        msg = f"Module {module.__name__} manifest name {manifest.name!r} must match configured name {module_name!r}"
        raise RuntimeError(msg)

    return manifest


async def load_modules(
    dp: Dispatcher | Router,
    to_load: Sequence[str],
    to_not_load: Sequence[str] = (),
    register_handlers: bool = True,
    registry: LoadedModuleRegistry | None = None,
) -> LoadedModuleRegistry:
    log.info("Importing modules...")
    active_registry = set_loaded_module_registry(registry or create_loaded_module_registry())
    active_registry.modules.clear()
    active_registry.api_routers.clear()

    if "*" in to_load:
        log.debug("Loading all modules...", modules=MODULES)
        to_load = MODULES
    else:
        log.info("Loading modules", to_load=to_load)

    for module_name in (x for x in MODULES if x in to_load and x not in to_not_load):
        path = f"sophie_bot.modules.{module_name}"

        module = import_module(path)

        manifest = get_loaded_module_manifest(module_name, module)

        if router := manifest.bot_router:
            dp.include_router(router)
        else:
            log.debug(f"! Module {module_name} has no router!")

        if api_router := manifest.api_router:
            active_registry.api_routers.append(api_router)

        active_registry.modules[manifest.name] = module

    if register_handlers:
        for module_name, module in active_registry.modules.items():
            log.debug(f"Loading module {module_name}...")
            # Load handlers
            manifest = get_module_manifest(module)
            if not (router := manifest.bot_router):
                continue

            for handler in manifest.handlers:
                log.debug(f"Registering handler {handler.__name__}...")
                handler.register(router)
    else:
        log.info("Skipping handler registration (register_handlers=False)")

    # Pre setup
    await gather(
        *(func() for module in active_registry.modules.values() if (func := get_module_manifest(module).pre_setup))
    )

    # Post setup
    await gather(
        *(
            func(active_registry.modules)
            for module in active_registry.modules.values()
            if (func := get_module_manifest(module).post_setup)
        )
    )

    log.info(f"Loaded modules - {', '.join(active_registry.modules.keys())}")
    return active_registry
