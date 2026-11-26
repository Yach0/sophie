from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiogram import Bot
from redis.asyncio import Redis

from sophie_bot.config import CONFIG, Config
from sophie_bot.db.cache.locale import LocaleStore
from sophie_bot.modules import LoadedModuleRegistry
from sophie_bot.modules.utils_.delayed_delete import DelayedDeletionService
from sophie_bot.services.db import DatabaseResources
from sophie_bot.utils.cached import RedisCache

if TYPE_CHECKING:
    from sophie_bot.modules.antispam.registry import ProviderRegistry


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
    config: Config = field(default_factory=lambda: CONFIG)
    antispam: ProviderRegistry | None = None

    async def close(self) -> None:
        tasks = tuple(self.background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.background_tasks.clear()
        if self.antispam is not None:
            await self.antispam.close()
        await self.deletions.close()
        await self.cache.close()
        await self.redis.aclose()
        await self.bot.session.close()
