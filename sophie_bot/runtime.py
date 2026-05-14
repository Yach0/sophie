from __future__ import annotations

from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from sophie_bot.modules import LoadedModuleRegistry, create_loaded_module_registry, set_loaded_module_registry
from sophie_bot.services.bot import BotRuntime, create_bot_runtime, set_bot_runtime
from sophie_bot.services.rest import create_app
from sophie_bot.services.scheduler import create_scheduler, set_scheduler


@dataclass(slots=True)
class BotModeRuntime:
    bot_runtime: BotRuntime
    loaded_modules: LoadedModuleRegistry


@dataclass(slots=True)
class RestModeRuntime:
    bot_runtime: BotRuntime
    loaded_modules: LoadedModuleRegistry
    app: FastAPI


@dataclass(slots=True)
class SchedulerModeRuntime:
    bot_runtime: BotRuntime
    loaded_modules: LoadedModuleRegistry
    scheduler: AsyncIOScheduler


def _create_loaded_modules() -> LoadedModuleRegistry:
    loaded_modules = create_loaded_module_registry()
    return set_loaded_module_registry(loaded_modules)


def build_bot_runtime() -> BotModeRuntime:
    bot_runtime = set_bot_runtime(create_bot_runtime())
    return BotModeRuntime(bot_runtime=bot_runtime, loaded_modules=_create_loaded_modules())


def build_rest_runtime() -> RestModeRuntime:
    bot_runtime = set_bot_runtime(create_bot_runtime())
    return RestModeRuntime(bot_runtime=bot_runtime, loaded_modules=_create_loaded_modules(), app=create_app())


def build_scheduler_runtime() -> SchedulerModeRuntime:
    bot_runtime = set_bot_runtime(create_bot_runtime())
    scheduler = set_scheduler(create_scheduler())
    return SchedulerModeRuntime(
        bot_runtime=bot_runtime,
        loaded_modules=_create_loaded_modules(),
        scheduler=scheduler,
    )
