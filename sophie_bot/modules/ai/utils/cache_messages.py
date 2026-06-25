from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from pydantic import BaseModel

from sophie_bot.services.redis import aredis

MESSAGE_CACHE_TTL = timedelta(hours=48)


class MessageType(BaseModel):
    user_id: int
    message_id: int
    text: str
    created_at: datetime | None = None
    username: str | None = None
    message_thread_id: int | None = None
    handled_by_ai: bool = False
    eligible_for_proactive_ai: bool = True
    reply_to_message_id: int | None = None
    reply_to_user_id: int | None = None
    reply_to_username: str | None = None
    reply_to_is_sophie_ai: bool = False
    has_ai_command: bool = False
    is_ai_filter_reply: bool = False
    proactively_answered: bool = False
    proactively_reacted: bool = False


def get_message_cache_key(chat_id: int) -> str:
    """Builds the Redis key for storing messages of a given chat."""
    return f"messages:{chat_id}"


def _build_cutoff(now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    return current_time - MESSAGE_CACHE_TTL


async def cache_message(
    text: Optional[str],
    chat_id: int,
    user_id: int,
    message_id: int,
    created_at: datetime,
    username: str | None,
    *,
    message_thread_id: int | None = None,
    handled_by_ai: bool = False,
    eligible_for_proactive_ai: bool = True,
    reply_to_message_id: int | None = None,
    reply_to_user_id: int | None = None,
    reply_to_username: str | None = None,
    reply_to_is_sophie_ai: bool = False,
    has_ai_command: bool = False,
    is_ai_filter_reply: bool = False,
    proactively_answered: bool = False,
    proactively_reacted: bool = False,
) -> None:
    """Caches a message if text is provided."""
    if not text:
        return

    msg = MessageType(
        user_id=user_id,
        message_id=message_id,
        text=text,
        created_at=created_at,
        username=username,
        message_thread_id=message_thread_id,
        handled_by_ai=handled_by_ai,
        eligible_for_proactive_ai=eligible_for_proactive_ai,
        reply_to_message_id=reply_to_message_id,
        reply_to_user_id=reply_to_user_id,
        reply_to_username=reply_to_username,
        reply_to_is_sophie_ai=reply_to_is_sophie_ai,
        has_ai_command=has_ai_command,
        is_ai_filter_reply=is_ai_filter_reply,
        proactively_answered=proactively_answered,
        proactively_reacted=proactively_reacted,
    )
    json_str = msg.model_dump_json()
    key = get_message_cache_key(chat_id)
    message_score = created_at.timestamp()
    cutoff_score = _build_cutoff(created_at).timestamp()

    async with aredis.pipeline(transaction=True) as pipe:
        await pipe.zadd(key, {json_str: message_score})  # type: ignore[misc]
        await pipe.zremrangebyscore(key, 0, cutoff_score)  # type: ignore[misc]
        await pipe.expire(key, 86400 * 2, lt=True)
        await pipe.execute()


async def reset_messages(chat_id: int) -> None:
    """Resets the cached messages for a given chat."""
    key = get_message_cache_key(chat_id)
    await aredis.delete(key)


def _parse_cached_message(raw_message: object) -> MessageType | None:
    if not isinstance(raw_message, (str, bytes, bytearray)):
        return None
    return MessageType.model_validate_json(raw_message)


async def get_cached_messages_between(chat_id: int, start_at: datetime, end_at: datetime) -> Tuple[MessageType, ...]:
    """Retrieve cached messages in a given inclusive time window."""
    key = get_message_cache_key(chat_id)
    raw_messages = await aredis.zrangebyscore(  # type: ignore[misc]
        key, start_at.timestamp(), end_at.timestamp()
    )
    messages = [message for raw_message in raw_messages if (message := _parse_cached_message(raw_message))]
    valid_messages = [
        message for message in messages if message.created_at and start_at <= message.created_at <= end_at
    ]
    return tuple(sorted(valid_messages, key=lambda message: (message.created_at, message.message_id)))


async def get_cached_messages(
    chat_id: int,
    now: datetime | None = None,
    limit: int | None = None,
    max_age: timedelta | None = None,
) -> Tuple[MessageType, ...]:
    """Retrieves and parses cached messages for a given chat.

    ``max_age`` further restricts the window to messages newer than ``now - max_age`` (never
    older than the cache TTL cutoff), on top of the optional trailing-``limit`` count cap.
    """
    current_time = now or datetime.now(timezone.utc)
    start_at = _build_cutoff(current_time)
    if max_age is not None:
        start_at = max(start_at, current_time - max_age)
    messages = await get_cached_messages_between(chat_id, start_at, current_time)
    if limit is None:
        return messages
    start_index = max(len(messages) - limit, 0)
    return messages[start_index:]
