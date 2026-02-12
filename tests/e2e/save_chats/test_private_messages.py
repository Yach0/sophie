"""Tests for private message handling in SaveChatsMiddleware.

This module tests how the middleware handles private messages between users and the bot.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType


class TestPrivateMessageHandling:
    """Test handling of private messages."""

    @pytest.mark.asyncio
    async def test_private_message_creates_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that private message creates/updates user in database."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", username="testuser", is_bot=False)
        chat = Chat(id=123456789, type="private", first_name="Test", username="testuser")
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
        assert "chat_db" in base_data
        assert "user_db" in base_data
        assert base_data["chat_db"] == base_data["user_db"]

        # Verify user was created in database
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None
        assert db_user.tid == 123456789
        assert db_user.first_name_or_title == "Test"
        assert db_user.username == "testuser"
        assert db_user.type == ChatType.private

    @pytest.mark.asyncio
    async def test_private_message_updates_existing_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that private message updates existing user data."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create existing user
        existing_user = ChatModel(
            tid=123456789,
            type=ChatType.private,
            first_name_or_title="OldName",
            username="oldusername",
            is_bot=False,
            last_saw=datetime.now(timezone.utc),
        )
        await existing_user.save()

        # Now send message with updated info
        user = User(id=123456789, first_name="NewName", username="newusername", is_bot=False)
        chat = Chat(id=123456789, type="private", first_name="NewName", username="newusername")
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
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None
        assert db_user.first_name_or_title == "NewName"
        assert db_user.username == "newusername"

    @pytest.mark.asyncio
    async def test_private_message_with_bot_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that private message from bot is handled correctly."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        bot_user = User(id=123456789, first_name="Bot", is_bot=True)
        chat = Chat(id=123456789, type="private", first_name="Bot")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=bot_user,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = bot_user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None
        assert db_user.is_bot is True

    @pytest.mark.asyncio
    async def test_private_message_without_username(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that private message from user without username is handled."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=123456789, type="private", first_name="Test")
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
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None
        assert db_user.username is None

    @pytest.mark.asyncio
    async def test_private_message_with_last_name(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that private message captures last name."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="John", last_name="Doe", is_bot=False)
        chat = Chat(id=123456789, type="private", first_name="John")
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
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None
        assert db_user.first_name_or_title == "John"
        assert db_user.last_name == "Doe"
