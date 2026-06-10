from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import cast

import sentry_sdk
from aiogram.types import Message

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages
from sophie_bot.modules.ai.utils.feature_settings import ProactiveReplySettings
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

_ELIGIBLE_KEY_TEMPLATE = "ai:proactive:{chat_tid}:eligible"
_LOCK_KEY_TEMPLATE = "ai:proactive:{chat_tid}:lock"
_LOCK_TTL_SECONDS = 120
_PROCESSED_TTL_SECONDS = 86400


def log_proactive_info(message: str, **data: object) -> None:
    log.info(message, **data)
    sentry_sdk.add_breadcrumb(
        category="ai.proactive_replies",
        message=message,
        level="info",
        data=data,
    )


def eligible_key(chat_tid: int) -> str:
    return _ELIGIBLE_KEY_TEMPLATE.format(chat_tid=chat_tid)


def lock_key(chat_tid: int) -> str:
    return _LOCK_KEY_TEMPLATE.format(chat_tid=chat_tid)


def is_candidate(message: MessageType) -> bool:
    return bool(
        message.eligible_for_proactive_ai
        and not message.handled_by_ai
        and not message.has_ai_command
        and not message.reply_to_is_sophie_ai
        and message.user_id != CONFIG.bot_id
    )


async def get_recent_candidates(chat_tid: int, settings: ProactiveReplySettings) -> tuple[MessageType, ...]:
    now = datetime.now(timezone.utc)
    messages = await get_cached_messages(chat_tid, now=now)
    min_created_at = now - timedelta(seconds=settings.window_seconds)
    candidates = tuple(
        message
        for message in messages
        if message.created_at and message.created_at >= min_created_at and is_candidate(message)
    )
    selected_candidates = candidates[-settings.batch_size :]
    log_proactive_info(
        "Proactive AI candidates loaded",
        chat_id=chat_tid,
        cached_messages=len(messages),
        eligible_candidates=len(candidates),
        selected_candidates=len(selected_candidates),
        window_seconds=settings.window_seconds,
    )
    return selected_candidates


async def track_eligible_message(chat_tid: int, message: Message, settings: ProactiveReplySettings) -> int:
    key = eligible_key(chat_tid)
    cutoff_score = (datetime.now(timezone.utc) - timedelta(seconds=settings.window_seconds)).timestamp()
    async with aredis.pipeline(transaction=True) as pipe:
        await pipe.zadd(key, {str(message.message_id): message.date.timestamp()})  # type: ignore[misc]
        await pipe.zremrangebyscore(key, 0, cutoff_score)  # type: ignore[misc]
        await pipe.expire(key, _PROCESSED_TTL_SECONDS, lt=True)
        await pipe.zcard(key)  # type: ignore[misc]
        results = await pipe.execute()
    tracked_count = int(results[-1])
    log_proactive_info(
        "Proactive AI eligible message tracked",
        chat_id=chat_tid,
        message_id=message.message_id,
        tracked_count=tracked_count,
        window_seconds=settings.window_seconds,
    )
    return tracked_count


async def clear_tracked_messages(chat_tid: int, messages: tuple[MessageType, ...]) -> None:
    if not messages:
        return
    key = eligible_key(chat_tid)
    await aredis.zrem(key, *(str(message.message_id) for message in messages))
    log_proactive_info("Proactive AI tracked messages cleared", chat_id=chat_tid, message_count=len(messages))


async def acquire_lock(chat_tid: int) -> bool:
    acquired = bool(
        await cast(Awaitable[bool | None], aredis.set(lock_key(chat_tid), "1", ex=_LOCK_TTL_SECONDS, nx=True))
    )
    log_proactive_info("Proactive AI lock state resolved", chat_id=chat_tid, acquired=acquired)
    return acquired


async def release_lock(chat_tid: int) -> None:
    await aredis.delete(lock_key(chat_tid))
    log_proactive_info("Proactive AI lock released", chat_id=chat_tid)
