from __future__ import annotations

from asyncio import gather
from dataclasses import dataclass, field
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Iterator, Sequence, Type, Union

from aiogram import Dispatcher, Router

from sophie_bot.utils.logger import log

if TYPE_CHECKING:
    from fastapi import APIRouter

    from sophie_bot.utils.handlers import SophieBaseHandler


@dataclass(slots=True)
class LoadedModuleRegistry:
    modules: dict[str, ModuleType] = field(default_factory=dict)
    api_routers: list["APIRouter"] = field(default_factory=list)


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
    def _api_routers(self) -> list["APIRouter"]:
        return get_loaded_module_registry().api_routers

    def __getitem__(self, index: int) -> "APIRouter":
        return self._api_routers()[index]

    def __len__(self) -> int:
        return len(self._api_routers())

    def __iter__(self) -> Iterator["APIRouter"]:
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


async def load_modules(
    dp: Union[Dispatcher, Router],
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

        if router := getattr(module, "router", None):
            dp.include_router(router)
        else:
            log.debug(f"! Module {module_name} has no router!")

        if api_router := getattr(module, "api_router", None):
            active_registry.api_routers.append(api_router)

        active_registry.modules[module.__name__.split(".", 3)[2]] = module

    if register_handlers:
        for module_name, module in active_registry.modules.items():
            log.debug(f"Loading module {module_name}...")
            # Load handlers
            if not (router := getattr(module, "router", None)):
                continue

            handlers: Sequence[Type["SophieBaseHandler"]] = getattr(module, "__handlers__", [])
            for handler in handlers:
                log.debug(f"Registering handler {handler.__name__}...")
                handler.register(router)
    else:
        log.info("Skipping handler registration (register_handlers=False)")

    # Pre setup
    await gather(
        *(
            func()
            for module_name, module in active_registry.modules.items()
            if (func := getattr(module, "__pre_setup__", None))
        )
    )

    # Post setup
    await gather(
        *(
            func(active_registry.modules)
            for module_name, module in active_registry.modules.items()
            if (func := getattr(module, "__post_setup__", None))
        )
    )

    log.info(f"Loaded modules - {', '.join(active_registry.modules.keys())}")
    return active_registry
