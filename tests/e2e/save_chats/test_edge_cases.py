"""Tests for edge cases and error handling in SaveChatsMiddleware.

This module tests various edge cases, error conditions, and boundary scenarios.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.db.models.chat import ChatModel


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_message_without_from_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling message without from_user (channel posts)."""
        from aiogram.types import Chat, Message, Update

        # Arrange
        chat = Chat(id=-1001234567890, type="channel", title="Test Channel")
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            # No from_user
        )

        update = Update(update_id=1, message=message)
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_update_from_user_only(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that update with only event_from_user saves the user."""
        from aiogram.types import InlineQuery, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", username="testuser", is_bot=False)

        inline_query = InlineQuery(
            id="test_query",
            from_user=user,
            query="test",
            offset="",
        )

        update = Update(update_id=1, inline_query=inline_query)
        base_data["event_from_user"] = user

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_user is not None

    @pytest.mark.asyncio
    async def test_reply_to_forum_topic_created(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that reply to forum topic created message is handled correctly."""
        from aiogram.types import Chat, ForumTopicCreated, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Forum", is_forum=True)

        # ForumTopicCreated requires icon_color field
        forum_topic_created = ForumTopicCreated(name="Test Topic", icon_color=0x6FB9F0)

        reply_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            forum_topic_created=forum_topic_created,
        )

        message = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            reply_to_message=reply_message,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Should not raise exception
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_very_long_group_title(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test group with very long title (within model limits)."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        # ChatModel limits first_name_or_title to 128 characters
        long_title = "A" * 128
        chat = Chat(id=-1001234567890, type="supergroup", title=long_title)

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
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None

    @pytest.mark.asyncio
    async def test_unicode_names(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling of unicode names (emoji, non-latin)."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(
            id=123456789,
            first_name="🎉 Party 🎊",
            last_name="日本語",
            username="testuser",
            is_bot=False,
        )
        chat = Chat(id=-1001234567890, type="supergroup", title="Группа на русском 🇷🇺")

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
        assert "🎉" in db_user.first_name_or_title

    @pytest.mark.asyncio
    async def test_special_characters_in_username(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling of special characters in username."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(
            id=123456789,
            first_name="Test",
            username="test_user_123",  # Underscores are allowed
            is_bot=False,
        )
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

    @pytest.mark.asyncio
    async def test_negative_user_id(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling of negative user ID (shouldn't happen but test it)."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - This is an edge case that shouldn't occur in real Telegram
        # but we test it for robustness
        user = User(id=-1, first_name="Test", is_bot=False)
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

        # Act - Should not raise exception
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_zero_user_id(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling of zero user ID."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=0, first_name="Test", is_bot=False)
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

        # Act - Should not raise exception
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_multiple_updates_same_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test multiple updates from the same user in quick succession."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        # Act - Send multiple messages
        for i in range(5):
            message = Message(
                message_id=i,
                date=datetime.now(timezone.utc),
                chat=chat,
                from_user=user,
            )
            update = Update(update_id=i, message=message)
            base_data["event_from_user"] = user
            base_data["event_chat"] = chat
            await middleware(mock_handler, update, base_data.copy())

        # Assert - Should only have one user
        users = await ChatModel.find(ChatModel.tid == 123456789).to_list()
        assert len(users) == 1

    @pytest.mark.asyncio
    async def test_empty_group_title(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test group with empty title."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act - Should not raise exception
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_none_values_in_user_data(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test user with None values for optional fields."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(
            id=123456789,
            first_name="Test",
            last_name=None,
            username=None,
            language_code=None,
            is_bot=False,
        )
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
    async def test_concurrent_updates(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling of concurrent updates."""
        import asyncio

        from aiogram.types import Chat, Message, Update, User

        # Arrange
        async def send_message(user_id: int, message_id: int):
            user = User(id=user_id, first_name=f"User{user_id}", is_bot=False)
            chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")
            message = Message(
                message_id=message_id,
                date=datetime.now(timezone.utc),
                chat=chat,
                from_user=user,
            )
            update = Update(update_id=message_id, message=message)
            data = base_data.copy()
            data["event_from_user"] = user
            data["event_chat"] = chat
            return await middleware(mock_handler, update, data)

        # Act - Send multiple messages concurrently
        tasks = [send_message(100 + i, i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # Assert
        assert all(r == "handler_result" for r in results)
        # Should have 10 different users
        users = await ChatModel.find(
            ChatModel.tid >= 100,
            ChatModel.tid < 110,
        ).to_list()
        assert len(users) == 10

    @pytest.mark.asyncio
    async def test_rapid_updates_same_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test rapid updates to the same chat."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        # Act - Rapid updates
        for i in range(20):
            user = User(id=1000 + i, first_name=f"User{i}", is_bot=False)
            message = Message(
                message_id=i,
                date=datetime.now(timezone.utc),
                chat=chat,
                from_user=user,
            )
            update = Update(update_id=i, message=message)
            base_data["event_from_user"] = user
            base_data["event_chat"] = chat
            await middleware(mock_handler, update, base_data.copy())

        # Assert
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert db_group.first_name_or_title == "Test Group"
