from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiogram.types import Message
from stfu_tg import KeyValue, Section, UserLink

from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils.logging import add_offending_message_text
from sophie_bot.utils.i18n import gettext as _


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
