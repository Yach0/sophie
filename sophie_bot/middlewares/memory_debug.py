import tracemalloc
from collections.abc import Awaitable, Callable
from random import randint
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from mem_top import mem_top

from sophie_bot.utils.logger import log


class TracemallocMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.first_snapshot: tracemalloc.Snapshot | None = None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)

        if not self.first_snapshot:
            self.first_snapshot = tracemalloc.take_snapshot()
            log.info("Tracemalloc: first snapshot taken...")
            return result

        # 1% that it'll handle
        if randint(0, 300) == 5:
            return result

        log.info("Mem top: \n" + mem_top(width=500))

        return result
