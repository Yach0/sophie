"""Sophie Bot initialization pipeline.

Provides composable initialization steps that each mode can select from,
avoiding loading unnecessary components (e.g., bot handlers in REST mode).
"""

from __future__ import annotations

from asyncio import gather

from aiogram import Dispatcher

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules import load_modules
from sophie_bot.services.bot import bot
from sophie_bot.services.db import init_db
from sophie_bot.services.migrations import run_migrations
from sophie_bot.utils.logger import log


async def init_database() -> None:
    """Initialize database connection, run migrations, then sync indexes."""
    await init_db(skip_indexes=True)
    await run_migrations()

    # After migrations are done, sync indexes
    await init_db()


async def init_modules_bot(dp: Dispatcher) -> None:
    """Load all modules with bot handlers registered."""
    await load_modules(dp, ["*"], CONFIG.modules_not_load, register_handlers=True)


async def init_modules_rest(dp: Dispatcher) -> None:
    """Load modules for REST mode: API routers only, no handler registration."""
    await load_modules(dp, ["*"], CONFIG.modules_not_load, register_handlers=False)


async def init_modules_scheduler(dp: Dispatcher) -> None:
    """Load modules for scheduler mode: scheduled jobs only, no handler registration."""
    await load_modules(dp, ["*"], CONFIG.modules_not_load, register_handlers=False)


async def ensure_bot_in_db() -> None:
    """Register or update the bot user record in the database."""
    bot_user = await bot.get_me()
    await ChatModel.upsert_user(bot_user)
    log.info("Bot user ensured in DB", bot_id=bot_user.id, username=bot_user.username)


async def start_init(dp: Dispatcher) -> None:
    """Initialize database, run migrations, and load modules.

    Backward-compatible entry point equivalent to full bot-mode initialization.
    """
    await init_database()
    await gather(ensure_bot_in_db(), init_modules_bot(dp))
