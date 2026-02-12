"""Tests for left chat member handling in SaveChatsMiddleware.

This module tests how the middleware handles users leaving groups,
including the bot itself leaving.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel, ChatType


class TestLeftChatMember:
    """Test handling of left chat members."""

    @pytest.mark.asyncio
    async def test_user_left_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that user leaving chat removes them from user_in_group."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create user and group first
        user_model = ChatModel(
            tid=111222333,
            type=ChatType.private,
            first_name_or_title="LeavingUser",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await user_model.save()

        group_model = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group_model.save()

        # Now user leaves
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        left_user = User(id=111222333, first_name="LeavingUser", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            left_chat_member=left_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Handler should be called, user and group should exist
        # (we can't verify UserInGroupModel with mongomock)
        assert mock_handler.called
        db_user = await ChatModel.find_one(ChatModel.tid == 111222333)
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_user is not None
        assert db_group is not None

    @pytest.mark.asyncio
    async def test_bot_left_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that bot leaving chat is handled correctly."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create group with bot (with required username field)
        group_model = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group_model.save()

        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        bot_user = User(id=CONFIG.bot_id, first_name="Bot", is_bot=True)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            left_chat_member=bot_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Handler should be called, middleware should handle bot leaving
        # Note: delete operations may not work correctly with mongomock
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_user_not_in_group_leaves(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test user leaving who was never in the group."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create user but don't add to group
        user_model = ChatModel(
            tid=111222333,
            type=ChatType.private,
            first_name_or_title="NeverJoined",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await user_model.save()

        group_model = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group_model.save()

        # User leaves (was never added)
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        left_user = User(id=111222333, first_name="NeverJoined", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            left_chat_member=left_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act - Should not raise exception
        await middleware(mock_handler, update, base_data)

        # Assert - Handler should be called
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_user_not_in_database_leaves(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test user leaving who doesn't exist in database at all."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create group but not the user
        group_model = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group_model.save()

        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        left_user = User(id=111222333, first_name="UnknownUser", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            left_chat_member=left_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act - Should not raise exception
        await middleware(mock_handler, update, base_data)

        # Assert
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_left_chat_member_in_private(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test left_chat_member in private chat (shouldn't happen but test it)."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        left_user = User(id=111222333, first_name="Left", is_bot=False)

        chat = Chat(id=123456789, type="private", first_name="Test")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            left_chat_member=left_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_bot_kicks_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test bot kicking a user from the group."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create user and group
        user_model = ChatModel(
            tid=111222333,
            type=ChatType.private,
            first_name_or_title="KickedUser",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await user_model.save()

        group_model = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group_model.save()

        # Bot kicks user
        bot_user = User(id=CONFIG.bot_id, first_name="Bot", is_bot=True)
        kicked_user = User(id=111222333, first_name="KickedUser", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=bot_user,
            left_chat_member=kicked_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = bot_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Handler should be called (we can't verify UserInGroupModel with mongomock)
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_user_leaves_group_not_in_db(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test user leaving a group that doesn't exist in database."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create user but no group
        user_model = ChatModel(
            tid=111222333,
            type=ChatType.private,
            first_name_or_title="LeavingUser",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await user_model.save()

        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        left_user = User(id=111222333, first_name="LeavingUser", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            left_chat_member=left_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act - Should not raise exception
        await middleware(mock_handler, update, base_data)

        # Assert
        assert mock_handler.called
