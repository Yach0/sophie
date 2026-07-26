from __future__ import annotations

import asyncio

import structlog

from sophie_bot.constants import SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot

_log = structlog.get_logger(__name__)

# asyncio only holds a weak reference to running tasks, so a fire-and-forget deletion can be
# garbage-collected mid-sleep. Keeping the task here until it finishes is what makes it reliable.
_background_tasks: set[asyncio.Task] = set()


def _task_done_callback(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if not task.cancelled() and (exc := task.exception()):
        _log.error("Delayed message deletion failed", exc_info=exc)


def schedule_message_deletion(
    chat_tid: int,
    message_ids: list[int],
    delay_seconds: int = SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS,
) -> None:
    """Delete the given messages after a delay, without blocking the caller.

    The wait is in-process, so a restart within the delay leaves the messages in place.
    """
    if not message_ids:
        return

    task = asyncio.create_task(delete_messages_after_delay(chat_tid, message_ids, delay_seconds=delay_seconds))
    _background_tasks.add(task)
    task.add_done_callback(_task_done_callback)


async def delete_messages_after_delay(
    chat_tid: int,
    message_ids: list[int],
    delay_seconds: int = SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS,
) -> None:
    await asyncio.sleep(delay_seconds)
    await common_try(bot.delete_messages(chat_tid, message_ids))
