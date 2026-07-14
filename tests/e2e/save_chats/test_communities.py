"""Tests for community handling in SaveChatsMiddleware (Bot API 10.2).

Mirrors the forum-topic tests: the middleware builds Sophie's own community→chats
registry from ``community_chat_added`` / ``community_chat_removed`` service messages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.communities import CommunityModel


@pytest.fixture(autouse=True)
def _enable_communities_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the communities feature flag on for these tests."""
    monkeypatch.setattr(
        "sophie_bot.middlewares.save_chats.is_enabled",
        AsyncMock(return_value=True),
    )


class TestSaveCommunity:
    @pytest.mark.asyncio
    async def test_community_chat_added_records_membership(self, middleware, mock_handler, base_data) -> None:
        from aiogram.types import Chat, Message, Update, User

        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Community Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            community_chat_added={"id": 555000, "name": "Cool Community"},
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        await middleware(mock_handler, update, base_data)

        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert db_group.type == ChatType.supergroup
        assert db_group.community_tid == 555000

        community = await CommunityModel.find_one(CommunityModel.community_tid == 555000)
        assert community is not None
        assert community.name == "Cool Community"
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_community_chat_removed_clears_membership(self, middleware, mock_handler, base_data) -> None:
        from aiogram.types import Chat, Message, Update, User

        # Pre-create a group that already belongs to a community.
        group = ChatModel(
            tid=-1002222222222,
            type=ChatType.supergroup,
            first_name_or_title="Leaving Group",
            is_bot=False,
            username=None,
            community_tid=999000,
            last_saw=datetime.now(timezone.utc),
        )
        await group.save()

        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1002222222222, type="supergroup", title="Leaving Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            community_chat_removed={"id": 999000},
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        await middleware(mock_handler, update, base_data)

        db_group = await ChatModel.find_one(ChatModel.tid == -1002222222222)
        assert db_group is not None
        assert db_group.community_tid is None
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_flag_disabled_is_inert(self, middleware, mock_handler, base_data, monkeypatch) -> None:
        from aiogram.types import Chat, Message, Update, User

        monkeypatch.setattr(
            "sophie_bot.middlewares.save_chats.is_enabled",
            AsyncMock(return_value=False),
        )

        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1003333333333, type="supergroup", title="No Flag Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            community_chat_added={"id": 111000, "name": "Ignored"},
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        await middleware(mock_handler, update, base_data)

        db_group = await ChatModel.find_one(ChatModel.tid == -1003333333333)
        assert db_group is not None
        assert db_group.community_tid is None
        assert await CommunityModel.find_one(CommunityModel.community_tid == 111000) is None
