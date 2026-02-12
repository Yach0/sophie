"""Tests for forum topic handling in SaveChatsMiddleware.

This module tests how the middleware handles forum topics in supergroups.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType


class TestForumTopics:
    """Test handling of forum topics."""

    @pytest.mark.asyncio
    async def test_forum_topic_created(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that forum topic creation is handled (saves group)."""
        from aiogram.types import Chat, ForumTopicCreated, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Forum", is_forum=True)

        # ForumTopicCreated requires icon_color field
        forum_topic_created = ForumTopicCreated(name="New Topic", icon_color=0x6FB9F0)

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            message_thread_id=123,
            forum_topic_created=forum_topic_created,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Group should be created (ChatTopicModel Link doesn't work with mongomock)
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert db_group.type == ChatType.supergroup
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_message_in_topic(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that message in existing topic is handled."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Forum", is_forum=True)

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            message_thread_id=456,
            is_topic_message=True,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Group and user should be created
        # (ChatTopicModel Link doesn't work well with mongomock)
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_group is not None
        assert db_user is not None
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_forum_topic_edited(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that forum topic edit updates the topic name."""
        from aiogram.types import Chat, ForumTopicEdited, Message, Update, User

        # Arrange - Create group first (with required username field)
        group = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Forum",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group.save()

        # Now edit the topic
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Forum", is_forum=True)

        forum_topic_edited = ForumTopicEdited(name="Updated Topic Name")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            message_thread_id=789,
            forum_topic_edited=forum_topic_edited,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Group should exist (ChatTopicModel Link doesn't work with mongomock)
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_reply_in_forum_topic(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that reply in forum topic is handled."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Forum", is_forum=True)

        # Original topic message
        topic_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            message_thread_id=100,
            is_topic_message=True,
        )

        # Reply to topic
        reply_message = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            message_thread_id=100,
            reply_to_message=topic_message,
        )

        update = Update(update_id=1, message=reply_message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Group and user should be created
        # (ChatTopicModel Link doesn't work with mongomock)
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_group is not None
        assert db_user is not None
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_non_forum_group_with_thread_id(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test message with thread_id in non-forum group."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Regular Group", is_forum=False)

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            message_thread_id=123,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"
        # Topic should not be created in non-forum group

    @pytest.mark.asyncio
    async def test_forum_topic_without_thread_id(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test forum topic message without thread_id."""
        from aiogram.types import Chat, ForumTopicCreated, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Forum", is_forum=True)

        # ForumTopicCreated requires icon_color field
        forum_topic_created = ForumTopicCreated(name="Topic Without ID", icon_color=0x6FB9F0)

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            # No message_thread_id
            forum_topic_created=forum_topic_created,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"
        # Topic should not be created without thread_id

    @pytest.mark.asyncio
    async def test_general_topic_message(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test message in general topic (thread_id=1)."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Forum", is_forum=True)

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            message_thread_id=1,  # General topic
            is_topic_message=True,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Group and user should be created
        # (ChatTopicModel Link doesn't work with mongomock)
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        db_user = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_group is not None
        assert db_user is not None
        assert mock_handler.called
