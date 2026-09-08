from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot

from sophie_bot.constants import SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
from sophie_bot.modules.utils_.common_try import common_try

_log = structlog.get_logger(__name__)


class DelayedDeletionService:
    """Own non-durable delayed Telegram deletion tasks for one application."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._background_tasks: set[asyncio.Task[None]] = set()

    def schedule(
        self,
        chat_tid: int,
        message_ids: list[int],
        delay_seconds: int = SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS,
    ) -> None:
        if not message_ids:
            return
        task = asyncio.create_task(self.delete_messages_after_delay(chat_tid, message_ids, delay_seconds=delay_seconds))
        self._background_tasks.add(task)
        task.add_done_callback(self._task_done_callback)

    def _task_done_callback(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()):
            _log.error("Delayed message deletion failed", exc_info=error)

    async def delete_messages_after_delay(
        self,
        chat_tid: int,
        message_ids: list[int],
        delay_seconds: int = SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS,
    ) -> None:
        await asyncio.sleep(delay_seconds)
        await common_try(self.bot.delete_messages(chat_tid, message_ids))

    async def close(self) -> None:
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
