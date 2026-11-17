from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sentry_sdk
from aiogram.types import Animation, Audio, Document, Message, PhotoSize, Sticker, Video, VideoNote, Voice
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import WatchError

MESSAGE_CACHE_TTL = timedelta(hours=48)


class CachedMedia(BaseModel):
    """Telegram attachment references only; extraction belongs to AI input construction."""

    photo: list[PhotoSize] | None = None
    animation: Animation | None = None
    document: Document | None = None
    sticker: Sticker | None = None
    voice: Voice | None = None
    audio: Audio | None = None
    video: Video | None = None
    video_note: VideoNote | None = None

    @classmethod
    def from_message(cls, message: Message) -> CachedMedia | None:
        fields = {name: getattr(message, name) for name in cls.model_fields if getattr(message, name)}
        if message.photo:
            fields["photo"] = [message.photo[-1]]
        if message.document and message.document.mime_type != "image/gif":
            del fields["document"]
        return cls.model_validate(fields) if fields else None

    def as_message(self, row: MessageType, chat_tid: int) -> Message:
        return Message.model_validate(
            {
                "message_id": row.message_id,
                "date": row.created_at or datetime.now(UTC),
                "chat": {"id": chat_tid, "type": "supergroup"},
                "from": {"id": row.user_id, "is_bot": row.is_bot, "first_name": "Context"},
                "caption": row.text,
                "message_thread_id": row.message_thread_id,
                **self.model_dump(exclude_none=True),
            }
        )


class MessageType(BaseModel):
    user_id: int
    is_bot: bool = False
    from_sender_chat: bool = False
    message_id: int
    text: str
    media: CachedMedia | None = None
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
    eligible_for_memory: bool = False
    speaker_name: str = ""
    memory_branch_id: int | None = None

    @property
    def reply_to_user_name(self) -> str | None:
        return self.reply_to_username or (str(self.reply_to_user_id) if self.reply_to_user_id is not None else None)


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
    pipe.zremrangebyscore(key, 0, _build_cutoff().timestamp())  # type: ignore[misc]
    pipe.expire(key, int(MESSAGE_CACHE_TTL.total_seconds()))


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
    from_sender_chat: bool = False,
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
    media: CachedMedia | None = None,
    eligible_for_memory: bool = False,
    speaker_name: str = "",
    memory_branch_id: int | None = None,
) -> None:
    """Cache text and attachment references without downloading media."""
    if not text and media is None:
        return

    msg = MessageType(
        user_id=user_id,
        is_bot=is_bot,
        from_sender_chat=from_sender_chat,
        message_id=message_id,
        text=text or "",
        media=media,
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
        eligible_for_memory=eligible_for_memory,
        speaker_name=speaker_name,
        memory_branch_id=memory_branch_id,
    )
    await store_cached_message(msg, chat_id, redis=redis, expected_epoch=expected_epoch)


async def store_cached_message(
    message: MessageType,
    chat_tid: int,
    *,
    redis: Redis,
    expected_epoch: int | None = None,
    preserve_existing: bool = False,
) -> bool:
    """Upsert one message without duplicate bodies or restoring a reset history."""
    if message.created_at is None:
        raise ValueError("Caching a message requires its creation time")
    key = get_message_cache_key(chat_tid)
    epoch_key = get_context_epoch_key(chat_tid)
    while True:
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key, epoch_key)
                if expected_epoch is not None and int(await pipe.get(epoch_key) or 0) != expected_epoch:
                    await pipe.unwatch()
                    return False
                timestamp = message.created_at.timestamp()
                raw_rows = await pipe.zrangebyscore(key, timestamp - 0.001, timestamp + 0.001)
                duplicates = [
                    raw
                    for raw in raw_rows
                    if isinstance(raw, (str, bytes))
                    and (cached := _parse_cached_message(raw))
                    and cached.message_id == message.message_id
                ]
                selected = message
                if preserve_existing and duplicates:
                    existing = _parse_cached_message(duplicates[-1])
                    assert existing is not None
                    selected = existing.model_copy(
                        update={
                            "eligible_for_memory": message.eligible_for_memory,
                            "memory_branch_id": message.memory_branch_id,
                            "speaker_name": message.speaker_name or existing.speaker_name,
                        }
                    )
                pipe.multi()
                if duplicates:
                    pipe.zrem(key, *duplicates)  # type: ignore[misc]
                enqueue_cached_message(pipe, chat_tid, selected)
                await pipe.execute()
                return True
            except WatchError:
                continue


async def reset_messages(chat_id: int, *, redis: Redis) -> None:
    """Resets the cached messages for a given chat."""
    key = get_message_cache_key(chat_id)
    await redis.delete(key)


async def reset_modern_context(chat_tid: int, *, redis: Redis, clear_messages: bool = True) -> None:
    epoch_key = get_context_epoch_key(chat_tid)
    index_key = f"ai:modern_context:sessions:{chat_tid}"
    while True:
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(epoch_key, index_key)
                session_keys = await pipe.smembers(index_key)
                pipe.multi()
                pipe.incr(epoch_key)
                pipe.delete(index_key, *session_keys)
                if clear_messages:
                    pipe.delete(get_message_cache_key(chat_tid))
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
