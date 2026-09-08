from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from sophie_bot.config import Config
from sophie_bot.utils.logger import log
from sophie_bot.utils.update_sanitizer import sanitizing_json_loads


def create_bot(config: Config) -> Bot:
    bot_api = TelegramAPIServer.from_base(str(config.botapi_server)) if config.botapi_server else PRODUCTION
    session = AiohttpSession(api=bot_api, json_loads=sanitizing_json_loads)
    log.info(f"Using BotAPI server: {bot_api}")
    return Bot(token=config.token, default=DefaultBotProperties(parse_mode="html"), session=session)


def create_dispatcher(config: Config) -> tuple[Dispatcher, RedisStorage]:
    """Create bot-only workflow resources with a separately owned FSM Redis client."""
    fsm_redis = Redis(
        host=config.redis_host,
        port=config.redis_port,
        username=config.redis_username,
        password=config.redis_password,
        db=config.redis_db_states,
        decode_responses=False,
        single_connection_client=True,
    )
    storage = RedisStorage(
        redis=fsm_redis,
        key_builder=DefaultKeyBuilder(prefix=str(config.redis_db_fsm)),
    )
    dispatcher = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
    return dispatcher, storage
