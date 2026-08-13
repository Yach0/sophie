"""Regression test for Sentry SOPHIE-28X.

``RedisMediaGroupAggregator.add_into_group`` serialized the buffered ``Message``
with ``model_dump_json()``. When an update path leaves an aiogram ``Default``
sentinel as a field *value*, pydantic cannot serialize it and the album
middleware crashes with ``PydanticSerializationError``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from aiogram.client.default import Default
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, LinkPreviewOptions, Message, User
from fakeredis import FakeAsyncRedis
from pydantic_core import PydanticSerializationError

from sophie_bot.middlewares.media_group import RedisMediaGroupAggregator

CHAT_ID = -1002900000002
USER_ID = 929000002
MEDIA_GROUP_ID = "album-default-sentinel"


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis()
    try:
        yield client
    finally:
        await client.aclose()


def _storage_key() -> StorageKey:
    return StorageKey(
        bot_id=42,
        chat_id=CHAT_ID,
        user_id=USER_ID,
        destiny=MEDIA_GROUP_ID,
    )


def _message_with_default_sentinels(message_id: int) -> Message:
    """A message carrying ``Default`` sentinels as field values, both top-level and nested.

    ``Message`` is frozen and validates on construction, so the sentinels are
    injected the same way aiogram does it internally: through ``model_copy``.
    """
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=CHAT_ID, type="supergroup", title="Album Group"),
        from_user=User(id=USER_ID, is_bot=False, first_name="AlbumUser"),
        media_group_id=MEDIA_GROUP_ID,
        caption="album item",
        link_preview_options=LinkPreviewOptions(url="https://example.org"),
    )
    # Nested sentinel: aiogram fills the unset fields of child objects the same way.
    link_preview_options = message.link_preview_options.model_copy(
        update={"is_disabled": Default("link_preview_is_disabled")}
    )
    return message.model_copy(
        update={
            "has_protected_content": Default("protect_content"),
            "show_caption_above_media": Default("show_caption_above_media"),
            "link_preview_options": link_preview_options,
        }
    )


@pytest.mark.asyncio
async def test_add_into_group_round_trips_messages_with_default_sentinels(redis: FakeAsyncRedis) -> None:
    aggregator = RedisMediaGroupAggregator(redis)
    key = _storage_key()
    messages = [_message_with_default_sentinels(message_id) for message_id in (10, 11)]

    # Sanity check: plain serialization is exactly what used to crash.
    with pytest.raises(PydanticSerializationError):
        messages[0].model_dump_json()

    for message in messages:
        await aggregator.add_into_group(key, message)

    album = await aggregator.get_group(key, bot=None)  # type: ignore[arg-type]

    assert [message.message_id for message in album] == [10, 11]
    assert [message.caption for message in album] == ["album item", "album item"]
    assert all(message.media_group_id == MEDIA_GROUP_ID for message in album)
    for message in album:
        assert not [name for name, value in message if isinstance(value, Default)]
        assert message.has_protected_content is None
        assert message.show_caption_above_media is None
        # Nested sentinels are cleared too, and the rest of the child object survives.
        assert message.link_preview_options is not None
        assert message.link_preview_options.is_disabled is None
        assert message.link_preview_options.url == "https://example.org"
