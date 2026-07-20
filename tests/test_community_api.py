"""Unit tests for the Bot API 10.2 community detection adapter.

These prove the adapter works with native aiogram Community types (aiogram 3.30+).
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import Chat, Community, CommunityChatAdded, CommunityChatRemoved, Message, User

from sophie_bot.utils.community_api import (
    CommunityChangeKind,
    CommunityRef,
    extract_community_change,
)


def _message(**extra: object) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=-1001234567890, type="supergroup", title="Test"),
        from_user=User(id=123, first_name="Test", is_bot=False),
        **extra,
    )


def test_extract_added_with_community() -> None:
    change = extract_community_change(
        _message(community_chat_added=CommunityChatAdded(community=Community(id=555, name="My Community")))
    )
    assert change is not None
    assert change.kind is CommunityChangeKind.ADDED
    assert change.community == CommunityRef(id=555, name="My Community")


def test_extract_removed() -> None:
    change = extract_community_change(_message(community_chat_removed=CommunityChatRemoved()))
    assert change is not None
    assert change.kind is CommunityChangeKind.REMOVED
    assert change.community is None


def test_extract_none_for_plain_message() -> None:
    assert extract_community_change(_message()) is None
