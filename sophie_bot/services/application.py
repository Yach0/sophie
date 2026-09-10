from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram import Bot
from redis.asyncio import Redis

from sophie_bot.db.cache.locale import LocaleStore
from sophie_bot.modules import LoadedModuleRegistry
from sophie_bot.modules.utils_.delayed_delete import DelayedDeletionService
from sophie_bot.services.db import DatabaseResources
from sophie_bot.utils.cached import RedisCache


@dataclass(slots=True)
class ApplicationServices:
    bot: Bot
    redis: Redis
    db: DatabaseResources
    cache: RedisCache
    locales: LocaleStore
    deletions: DelayedDeletionService
    modules: LoadedModuleRegistry
    background_tasks: set[asyncio.Task[object]]

    async def close(self) -> None:
        tasks = tuple(self.background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.background_tasks.clear()
        await self.deletions.close()
        await self.cache.close()
        await self.redis.aclose()
        await self.bot.session.close()
