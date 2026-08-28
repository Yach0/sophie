"""Sophie Bot initialization pipeline.

Provides composable initialization steps that each mode can select from,
avoiding loading unnecessary components (e.g., bot handlers in REST mode).
"""

from __future__ import annotations

from asyncio import gather

from aiogram import Bot, Dispatcher

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules import LoadedModuleRegistry, load_modules
from sophie_bot.runtime import BotModeRuntime, RestModeRuntime, SchedulerModeRuntime
from sophie_bot.services.bot import get_bot_runtime
from sophie_bot.services.db import init_db
from sophie_bot.services.migrations import run_migrations
from sophie_bot.utils.logger import log


async def init_database() -> None:
    """Initialize database connection, run migrations, then sync indexes."""
    await init_db(skip_indexes=True)
    await run_migrations()

    # After migrations are done, sync indexes
    await init_db()


async def init_modules(
    dispatcher: Dispatcher,
    register_handlers: bool,
    loaded_modules: LoadedModuleRegistry | None = None,
) -> LoadedModuleRegistry:
    return await load_modules(
        dispatcher,
        ["*"],
        CONFIG.modules_not_load,
        register_handlers=register_handlers,
        registry=loaded_modules,
    )


async def init_modules_bot(dp: Dispatcher, loaded_modules: LoadedModuleRegistry | None = None) -> LoadedModuleRegistry:
    """Load all modules with bot handlers registered."""
    return await init_modules(dp, register_handlers=True, loaded_modules=loaded_modules)


async def init_modules_rest(dp: Dispatcher, loaded_modules: LoadedModuleRegistry | None = None) -> LoadedModuleRegistry:
    """Load modules for REST mode: API routers only, no handler registration."""
    return await init_modules(dp, register_handlers=False, loaded_modules=loaded_modules)


async def init_modules_scheduler(
    dp: Dispatcher, loaded_modules: LoadedModuleRegistry | None = None
) -> LoadedModuleRegistry:
    """Load modules for scheduler mode: scheduled jobs only, no handler registration."""
    return await init_modules(dp, register_handlers=False, loaded_modules=loaded_modules)


async def ensure_bot_in_db(bot: Bot) -> None:
    """Register or update the bot user record in the database."""
    bot_user = await bot.get_me()
    await ChatModel.upsert_user(bot_user)
    log.info("Bot user ensured in DB", bot_id=bot_user.id, username=bot_user.username)


async def initialize_bot_mode(runtime: BotModeRuntime) -> None:
    await init_database()
    await gather(
        ensure_bot_in_db(runtime.bot_runtime.bot),
        init_modules_bot(runtime.bot_runtime.dispatcher, runtime.loaded_modules),
    )


async def initialize_rest_mode(runtime: RestModeRuntime) -> None:
    await init_database()
    await init_modules_rest(runtime.bot_runtime.dispatcher, runtime.loaded_modules)


async def initialize_scheduler_mode(runtime: SchedulerModeRuntime) -> None:
    await init_database()
    await gather(
        ensure_bot_in_db(runtime.bot_runtime.bot),
        init_modules_scheduler(runtime.bot_runtime.dispatcher, runtime.loaded_modules),
    )


async def start_init(dp: Dispatcher) -> None:
    """Initialize database, run migrations, and load modules.

    Backward-compatible entry point equivalent to full bot-mode initialization.
    """
    await init_database()
    await gather(ensure_bot_in_db(get_bot_runtime().bot), init_modules_bot(dp))
