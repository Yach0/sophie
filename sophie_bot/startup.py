from __future__ import annotations

from asyncio import gather

from aiogram import Bot
from redis.asyncio import Redis

from sophie_bot.config import Config
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules import (
    assemble_api_modules,
    assemble_bot_modules,
    initialize_modules,
    register_module_jobs,
)
from sophie_bot.runtime import BotModeRuntime, RestModeRuntime, SchedulerModeRuntime
from sophie_bot.services.db import DatabaseResources, init_db
from sophie_bot.services.migrations import MigrationResources, run_migrations
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


async def init_database(database: DatabaseResources, redis: Redis, *, config: Config) -> None:
    """Initialize one Beanie owner once, with migrations before index synchronization."""
    async with database.initialization_lock:
        if database.initialized:
            return
        await init_db(database.database, config=config, skip_indexes=True)
        if config.run_migrations_on_startup:
            await run_migrations(MigrationResources(database=database, redis=redis))
        else:
            log.info("Migrations disabled by configuration")
        await init_db(database.database, config=config)
        database.initialized = True


async def ensure_architecture_enabled(redis: Redis) -> None:
    if not await is_enabled("architecture_refactor", redis=redis):
        raise RuntimeError("Architecture refactor is disabled; refusing to start this build")


async def ensure_bot_in_db(bot: Bot) -> None:
    bot_user = await bot.get_me()
    await ChatModel.upsert_user(bot_user)
    log.info("Bot user ensured in DB", bot_id=bot_user.id, username=bot_user.username)


async def initialize_bot_mode(runtime: BotModeRuntime) -> None:
    services = runtime.services
    await init_database(services.db, services.redis, config=runtime.config)
    await ensure_architecture_enabled(services.redis)
    await initialize_modules(services)
    await gather(ensure_bot_in_db(services.bot), assemble_bot_modules(runtime.dispatcher, services))


async def initialize_rest_mode(runtime: RestModeRuntime) -> None:
    services = runtime.services
    await init_database(services.db, services.redis, config=runtime.config)
    await ensure_architecture_enabled(services.redis)
    await initialize_modules(services)
    assemble_api_modules(runtime.app, services.modules)


async def initialize_scheduler_mode(runtime: SchedulerModeRuntime) -> None:
    services = runtime.services
    await init_database(services.db, services.redis, config=runtime.config)
    await ensure_architecture_enabled(services.redis)
    await initialize_modules(services)
    await ensure_bot_in_db(services.bot)
    register_module_jobs(runtime.scheduler, services)
