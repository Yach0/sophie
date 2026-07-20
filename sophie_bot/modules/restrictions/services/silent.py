from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import structlog
from aiogram.types import Message
from stfu_tg import KeyValue, Section, UserLink

from sophie_bot.constants import SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils.logging import add_offending_message_text
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.i18n import gettext as _

_log = structlog.get_logger(__name__)

_background_tasks: set[asyncio.Task] = set()


def _task_done_callback(task: asyncio.Task) -> None:
    """Remove completed task from the set and log exceptions."""
    _background_tasks.discard(task)
    if not task.cancelled() and (exc := task.exception()):
        _log.error("Silent mode message deletion failed", exc_info=exc)


def schedule_message_deletion(
    chat_tid: int,
    message_ids: list[int],
    delay_seconds: int = SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS,
) -> None:
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


async def log_silent_action(
    *,
    chat_tid: int,
    actor_user_id: int,
    event_type: LogEvent,
    target_user_id: int,
    reply_to_message: Message | None,
    reason: str | None,
    until_date: timedelta | None = None,
) -> None:
    payload: dict[str, Any] = {"target_user_id": target_user_id, "reason": reason}
    if until_date is not None:
        payload["duration"] = until_date.total_seconds()

    await log_event(chat_tid, actor_user_id, event_type, add_offending_message_text(payload, reply_to_message))


def build_silent_action_doc(
    *,
    chat_title: str,
    target_user_id: int,
    target_user_name: str,
    actor_user_id: int,
    actor_user_name: str,
    actor_label: str,
    title: str,
    reason: str | None,
    duration_text: str | None = None,
) -> Section:
    return Section(
        KeyValue(_("Chat"), chat_title),
        KeyValue(_("User"), UserLink(target_user_id, target_user_name)),
        KeyValue(actor_label, UserLink(actor_user_id, actor_user_name)),
        KeyValue(_("Duration"), duration_text) if duration_text else None,
        KeyValue(_("Reason"), reason) if reason else None,
        title=title,
    )


def collect_message_ids_for_cleanup(message: Message, reply_message_id: int) -> list[int]:
    message_ids = [message.message_id, reply_message_id]
    if message.reply_to_message:
        message_ids.append(message.reply_to_message.message_id)
    return message_ids
