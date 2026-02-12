"""Tests for group message handling in SaveChatsMiddleware.

This module tests how the middleware handles messages in groups and supergroups,
including user tracking and group metadata updates.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType


class TestGroupMessageHandling:
    """Test handling of group messages."""

    @pytest.mark.asyncio
    async def test_group_message_creates_group_and_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that group message creates/updates group and user."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="TestUser", username="testuser", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

        # Verify group was created
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert db_group.type == ChatType.supergroup
        assert db_group.first_name_or_title == "Test Group"

        # Verify user was created
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None
        assert db_user.type == ChatType.private

        # Note: UserInGroupModel Link relationships don't work well with mongomock
        # The middleware attempts to create these relationships, but we can't verify them here

    @pytest.mark.asyncio
    async def test_group_message_updates_existing_group(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that group message updates existing group data."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create existing group (with required username field)
        existing_group = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Old Group Name",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await existing_group.save()

        # Now send message with updated group info
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="New Group Name")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert db_group.first_name_or_title == "New Group Name"
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_regular_group_message(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that regular group (not supergroup) is handled correctly."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-123456789, type="group", title="Regular Group")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"
        db_group = await ChatModel.find_one(ChatModel.tid == -123456789)
        assert db_group is not None
        assert db_group.type == ChatType.group

    @pytest.mark.asyncio
    async def test_group_message_with_sender_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling message sent as channel/group (sender_chat)."""
        from aiogram.types import Chat, Message, Update

        # Arrange
        sender_chat = Chat(id=-1009876543210, type="channel", title="Test Channel")
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            sender_chat=sender_chat,
        )
        update = Update(update_id=1, message=message)
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - The middleware doesn't save sender_chat, only from_user
        # This is expected behavior - sender_chat is for anonymous/channel messages
        # and the middleware only tracks users who directly interact
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_group_message_with_sender_chat_same_as_group(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling message when sender_chat is the same as the group (anonymous admin)."""
        from aiogram.types import Chat, Message, Update

        # Arrange
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            sender_chat=chat,  # Same as chat
        )
        update = Update(update_id=1, message=message)
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"
        # Should not create duplicate user relationship

    @pytest.mark.asyncio
    async def test_group_with_username(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that group with public username is handled correctly."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Public Group", username="publicgroup")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert db_group.username == "publicgroup"

    @pytest.mark.asyncio
    async def test_multiple_users_in_same_group(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that multiple users in the same group are tracked correctly."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - First user sends message
        user1 = User(id=111111, first_name="User1", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")
        message1 = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user1,
        )
        update1 = Update(update_id=1, message=message1)
        base_data["event_from_user"] = user1
        base_data["event_chat"] = chat
        await middleware(mock_handler, update1, base_data.copy())

        # Second user sends message
        user2 = User(id=222222, first_name="User2", is_bot=False)
        message2 = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user2,
        )
        update2 = Update(update_id=2, message=message2)
        base_data["event_from_user"] = user2
        base_data["event_chat"] = chat
        await middleware(mock_handler, update2, base_data.copy())

        # Assert
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None

        # Verify both users were created
        db_user1 = await ChatModel.find_one(ChatModel.tid == 111111)
        db_user2 = await ChatModel.find_one(ChatModel.tid == 222222)
        assert db_user1 is not None
        assert db_user2 is not None

        # Note: UserInGroupModel Link relationships don't work well with mongomock
        # The middleware attempts to create these relationships, but we can't verify them here
