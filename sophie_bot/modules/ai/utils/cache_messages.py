from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sentry_sdk
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import WatchError

MESSAGE_CACHE_TTL = timedelta(hours=48)


class MessageType(BaseModel):
    user_id: int
    is_bot: bool = False
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

    @property
    def reply_to_user_name(self) -> str | None:
        if self.reply_to_username:
            return self.reply_to_username
        if self.reply_to_user_id is not None:
            return str(self.reply_to_user_id)
        return None


def get_message_cache_key(chat_id: int) -> str:
    """Builds the Redis key for storing messages of a given chat."""
    return f"messages:{chat_id}"


def get_context_epoch_key(chat_id: int) -> str:
    return f"ai:modern_context:epoch:{chat_id}"


def enqueue_cached_message(pipe: Pipeline, chat_id: int, message: MessageType) -> None:
    """Queue the same body cache writes inside a caller-owned transaction."""
    if message.created_at is None:
        raise ValueError("Caching a message requires its creation time")
    key = get_message_cache_key(chat_id)
    pipe.zadd(key, {message.model_dump_json(): message.created_at.timestamp()})  # type: ignore[misc]
    pipe.zremrangebyscore(key, 0, _build_cutoff(message.created_at).timestamp())  # type: ignore[misc]
    pipe.expire(key, int(MESSAGE_CACHE_TTL.total_seconds()), lt=True)


def _build_cutoff(now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(UTC)
    return current_time - MESSAGE_CACHE_TTL


async def cache_message(
    text: str | None,
    chat_id: int,
    user_id: int,
    message_id: int,
    created_at: datetime,
    username: str | None,
    *,
    redis: Redis,
    is_bot: bool = False,
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
    expected_epoch: int | None = None,
) -> None:
    """Caches a message if text is provided."""
    if not text:
        return

    msg = MessageType(
        user_id=user_id,
        is_bot=is_bot,
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
    while True:
        async with redis.pipeline(transaction=True) as pipe:
            try:
                if expected_epoch is not None:
                    epoch_key = get_context_epoch_key(chat_id)
                    await pipe.watch(epoch_key)
                    if int(await pipe.get(epoch_key) or 0) != expected_epoch:
                        await pipe.unwatch()
                        return
                    pipe.multi()
                enqueue_cached_message(pipe, chat_id, msg)
                await pipe.execute()
                return
            except WatchError:
                continue


async def reset_messages(chat_id: int, *, redis: Redis) -> None:
    """Resets the cached messages for a given chat."""
    key = get_message_cache_key(chat_id)
    await redis.delete(key)


async def reset_modern_context(chat_tid: int, *, redis: Redis) -> None:
    epoch_key = get_context_epoch_key(chat_tid)
    index_key = f"ai:modern_context:sessions:{chat_tid}"
    while True:
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(epoch_key, index_key)
                session_keys = await pipe.smembers(index_key)
                pipe.multi()
                pipe.incr(epoch_key)
                pipe.delete(index_key, get_message_cache_key(chat_tid), *session_keys)
                await pipe.execute()
                return
            except WatchError:
                continue


def _parse_cached_message(raw_message: object) -> MessageType | None:
    if not isinstance(raw_message, (str, bytes, bytearray)):
        return None
    return MessageType.model_validate_json(raw_message)


async def get_cached_messages_between(
    chat_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    redis: Redis,
) -> tuple[MessageType, ...]:
    """Retrieve cached messages in a given inclusive time window."""
    with sentry_sdk.start_span(op="ai.cache", name="Read cached messages") as span:
        key = get_message_cache_key(chat_id)
        raw_messages = await redis.zrangebyscore(  # type: ignore[misc]
            key, start_at.timestamp(), end_at.timestamp()
        )
        messages = [message for raw_message in raw_messages if (message := _parse_cached_message(raw_message))]
        valid_messages = [
            message for message in messages if message.created_at and start_at <= message.created_at <= end_at
        ]
        result = tuple(sorted(valid_messages, key=lambda message: (message.created_at, message.message_id)))
        span.set_data("ai.cache.hit", bool(result))
        span.set_data("ai.cache.message_count", len(result))
        return result


async def get_cached_messages(
    chat_id: int,
    now: datetime | None = None,
    limit: int | None = None,
    max_age: timedelta | None = None,
    *,
    redis: Redis,
) -> tuple[MessageType, ...]:
    """Retrieves and parses cached messages for a given chat.

    ``max_age`` further restricts the window to messages newer than ``now - max_age`` (never
    older than the cache TTL cutoff), on top of the optional trailing-``limit`` count cap.
    """
    current_time = now or datetime.now(UTC)
    start_at = _build_cutoff(current_time)
    if max_age is not None:
        start_at = max(start_at, current_time - max_age)
    messages = await get_cached_messages_between(chat_id, start_at, current_time, redis=redis)
    if limit is None:
        result = messages
    else:
        start_index = max(len(messages) - limit, 0)
        result = messages[start_index:]

    return result
