from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from sophie_bot.config import CONFIG
from sophie_bot.utils.logger import log
from sophie_bot.utils.runtime_proxy import RuntimeProxy


@dataclass(slots=True)
class BotRuntime:
    bot_api: TelegramAPIServer
    session: AiohttpSession
    bot: Bot
    redis: Redis
    storage: RedisStorage
    dispatcher: Dispatcher


def create_bot_runtime() -> BotRuntime:
    bot_api = TelegramAPIServer.from_base(str(CONFIG.botapi_server)) if CONFIG.botapi_server else PRODUCTION
    session = AiohttpSession(api=bot_api)
    log.info(f"Using BotAPI server: {bot_api}")

    bot = Bot(token=CONFIG.token, default=DefaultBotProperties(parse_mode="html"), session=session)
    redis = Redis(
        host=CONFIG.redis_host,
        port=CONFIG.redis_port,
        password=CONFIG.redis_password,
        db=CONFIG.redis_db_states,
        single_connection_client=True,
    )
    storage = RedisStorage(redis=redis, key_builder=DefaultKeyBuilder(prefix=str(CONFIG.redis_db_fsm)))
    dispatcher = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
    return BotRuntime(
        bot_api=bot_api,
        session=session,
        bot=bot,
        redis=redis,
        storage=storage,
        dispatcher=dispatcher,
    )


_bot_runtime: BotRuntime | None = None


def get_bot_runtime() -> BotRuntime:
    global _bot_runtime

    if _bot_runtime is None:
        _bot_runtime = create_bot_runtime()

    return _bot_runtime


def set_bot_runtime(runtime: BotRuntime) -> BotRuntime:
    global _bot_runtime

    _bot_runtime = runtime
    return runtime


bot = cast(Bot, RuntimeProxy(lambda: get_bot_runtime().bot))
redis = cast(Redis, RuntimeProxy(lambda: get_bot_runtime().redis))
storage = cast(RedisStorage, RuntimeProxy(lambda: get_bot_runtime().storage))
session = cast(AiohttpSession, RuntimeProxy(lambda: get_bot_runtime().session))
dp = cast(Dispatcher, RuntimeProxy(lambda: get_bot_runtime().dispatcher))
