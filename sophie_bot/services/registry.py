"""Service registry for Sophie Bot.

Provides lazy initialization and factory functions for all core services.
Tests can override services via `override()` without relying on import-time
hacks like checking `"pytest" in sys.modules`.
"""

from __future__ import annotations

import os
from typing import Any

from sophie_bot.config import CONFIG


class ServiceRegistry:
    """Central registry for all core services.

    Services are created lazily on first access. Tests can inject
    fakes/mocks via `override(name, instance)` before the service is
    first used, avoiding import-time side effects.
    """

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._overrides: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Override / reset API for tests
    # ------------------------------------------------------------------

    def override(self, name: str, instance: Any) -> None:
        """Override a service instance for testing.

        Args:
            name: The service name (e.g. "redis", "bot", "mongo", "dispatcher",
                  "mistral", "scheduler").
            instance: The replacement instance to use.
        """
        self._overrides[name] = instance
        # Invalidate cached instance so next access returns the override
        self._instances.pop(name, None)

    def reset(self) -> None:
        """Clear all overrides and cached instances.

        After calling this, the next access to any service will create a
        fresh instance using the default factory.
        """
        self._overrides.clear()
        self._instances.clear()

    # ------------------------------------------------------------------
    # Service accessors (lazy factories)
    # ------------------------------------------------------------------

    def get_redis(self) -> Any:
        """Get the async Redis client.

        Returns FakeAsyncRedis when TESTING=1 env var is set,
        otherwise a real Redis connection.
        """
        if "redis" in self._overrides:
            return self._overrides["redis"]

        if "redis" not in self._instances:
            self._instances["redis"] = self._create_redis()
        return self._instances["redis"]

    def get_mongo(self) -> Any:
        """Get the async MongoDB client."""
        if "mongo" in self._overrides:
            return self._overrides["mongo"]

        if "mongo" not in self._instances:
            self._instances["mongo"] = self._create_mongo()
        return self._instances["mongo"]

    def get_bot(self) -> Any:
        """Get the aiogram Bot instance."""
        if "bot" in self._overrides:
            return self._overrides["bot"]

        if "bot" not in self._instances:
            self._instances["bot"] = self._create_bot()
        return self._instances["bot"]

    def get_dispatcher(self) -> Any:
        """Get the aiogram Dispatcher instance."""
        if "dispatcher" in self._overrides:
            return self._overrides["dispatcher"]

        if "dispatcher" not in self._instances:
            self._instances["dispatcher"] = self._create_dispatcher()
        return self._instances["dispatcher"]

    def get_mistral(self) -> Any:
        """Get the Mistral AI client."""
        if "mistral" in self._overrides:
            return self._overrides["mistral"]

        if "mistral" not in self._instances:
            self._instances["mistral"] = self._create_mistral()
        return self._instances["mistral"]

    def get_scheduler(self) -> Any:
        """Get the APScheduler AsyncIOScheduler instance."""
        if "scheduler" in self._overrides:
            return self._overrides["scheduler"]

        if "scheduler" not in self._instances:
            self._instances["scheduler"] = self._create_scheduler()
        return self._instances["scheduler"]

    # ------------------------------------------------------------------
    # Factory methods (private)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_testing() -> bool:
        """Check if running in a test environment."""
        return os.environ.get("TESTING") == "1"

    def _create_redis(self) -> Any:
        """Create the Redis client based on environment."""
        if self._is_testing():
            from fakeredis import FakeAsyncRedis

            return FakeAsyncRedis(
                decode_responses=False,
                single_connection_client=True,
            )

        from redis.asyncio import Redis

        return Redis(
            host=CONFIG.redis_host,
            port=CONFIG.redis_port,
            username=CONFIG.redis_username,
            password=CONFIG.redis_password,
            db=CONFIG.redis_db_states,
            decode_responses=False,
            single_connection_client=True,
        )

    @staticmethod
    def _create_mongo() -> Any:
        """Create the async MongoDB client."""
        from pymongo import AsyncMongoClient

        return AsyncMongoClient(CONFIG.mongo_host, CONFIG.mongo_port)

    @staticmethod
    def _create_bot() -> Any:
        """Create the aiogram Bot instance."""
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import PRODUCTION, TelegramAPIServer

        from sophie_bot.utils.logger import log
        from sophie_bot.utils.update_sanitizer import sanitizing_json_loads

        bot_api = TelegramAPIServer.from_base(str(CONFIG.botapi_server)) if CONFIG.botapi_server else PRODUCTION
        session = AiohttpSession(api=bot_api, json_loads=sanitizing_json_loads)
        log.info(f"Using BotAPI server: {bot_api}")

        return Bot(
            token=CONFIG.token,
            default=DefaultBotProperties(parse_mode="html"),
            session=session,
        )

    @staticmethod
    def _create_dispatcher() -> Any:
        """Create the aiogram Dispatcher with Redis FSM storage."""
        from aiogram import Dispatcher
        from aiogram.fsm.storage.base import DefaultKeyBuilder
        from aiogram.fsm.storage.memory import SimpleEventIsolation
        from aiogram.fsm.storage.redis import RedisStorage
        from redis.asyncio import Redis

        redis = Redis(
            host=CONFIG.redis_host,
            port=CONFIG.redis_port,
            password=CONFIG.redis_password,
            db=CONFIG.redis_db_states,
            single_connection_client=True,
        )
        storage = RedisStorage(
            redis=redis,
            key_builder=DefaultKeyBuilder(prefix=str(CONFIG.redis_db_fsm)),
        )
        return Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())

    @staticmethod
    def _create_mistral() -> Any:
        """Create the Mistral AI client."""
        from mistralai.client.sdk import Mistral

        return Mistral(api_key=CONFIG.mistral_api_key or "")

    def _create_scheduler(self) -> Any:
        """Create the APScheduler AsyncIOScheduler instance."""
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.jobstores.mongodb import MongoDBJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        mongo_store = MongoDBJobStore(
            database=CONFIG.mongo_db,
            collection="jobs",
            host=CONFIG.mongo_host,
            port=CONFIG.mongo_port,
        )
        mem_store = MemoryJobStore()
        return AsyncIOScheduler(
            jobstores={"default": mongo_store, "ram": mem_store},
        )


# Module-level singleton registry instance
registry = ServiceRegistry()
