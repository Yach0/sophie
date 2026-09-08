from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aiogram import Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from sophie_bot.config import CONFIG, Config
from sophie_bot.db.cache.locale import LocaleStore
from sophie_bot.modules import discover_modules
from sophie_bot.modules.utils_.delayed_delete import DelayedDeletionService
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.bot import create_bot, create_dispatcher
from sophie_bot.services.db import DatabaseResources, open_database
from sophie_bot.services.i18n import i18n
from sophie_bot.services.redis import create_redis
from sophie_bot.services.scheduler import create_scheduler
from sophie_bot.utils.cached import RedisCache


@dataclass(slots=True)
class BotModeRuntime:
    config: Config
    services: ApplicationServices
    dispatcher: Dispatcher
    storage: RedisStorage


@dataclass(slots=True)
class RestModeRuntime:
    config: Config
    services: ApplicationServices
    app: FastAPI


@dataclass(slots=True)
class SchedulerModeRuntime:
    config: Config
    services: ApplicationServices
    scheduler: AsyncIOScheduler


@asynccontextmanager
async def _build_application_services(
    config: Config,
    database: DatabaseResources | None,
) -> AsyncIterator[ApplicationServices]:
    if database is None:
        async with (
            open_database(config) as owned_database,
            _build_application_services(config, owned_database) as services,
        ):
            yield services
        return

    bot = create_bot(config)
    redis = None
    services = None
    try:
        redis = create_redis(config)
        cache = RedisCache(redis)
        services = ApplicationServices(
            bot=bot,
            redis=redis,
            db=database,
            cache=cache,
            locales=LocaleStore(cache, i18n, config.default_locale),
            deletions=DelayedDeletionService(bot),
            modules=discover_modules(config.modules_load, config.modules_not_load),
            background_tasks=set(),
        )
        yield services
    finally:
        if services is not None:
            await services.close()
        else:
            if redis is not None:
                await redis.aclose()
            await bot.session.close()


@asynccontextmanager
async def build_bot_runtime(
    *,
    config: Config = CONFIG,
    database: DatabaseResources | None = None,
) -> AsyncIterator[BotModeRuntime]:
    async with _build_application_services(config, database) as services:
        dispatcher, storage = create_dispatcher(config)
        dispatcher.workflow_data["services"] = services
        try:
            yield BotModeRuntime(config=config, services=services, dispatcher=dispatcher, storage=storage)
        finally:
            await storage.close()


@asynccontextmanager
async def build_rest_runtime(
    app: FastAPI,
    *,
    config: Config = CONFIG,
    database: DatabaseResources | None = None,
) -> AsyncIterator[RestModeRuntime]:
    async with _build_application_services(config, database) as services:
        app.state.services = services
        try:
            yield RestModeRuntime(config=config, services=services, app=app)
        finally:
            app.state.services = None


@asynccontextmanager
async def build_scheduler_runtime(
    *,
    config: Config = CONFIG,
    database: DatabaseResources | None = None,
) -> AsyncIterator[SchedulerModeRuntime]:
    async with _build_application_services(config, database) as services:
        scheduler = create_scheduler(config)
        yield SchedulerModeRuntime(config=config, services=services, scheduler=scheduler)
