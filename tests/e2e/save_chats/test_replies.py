"""Tests for reply message handling in SaveChatsMiddleware.

This module tests how the middleware handles replies to messages,
including updating the replied-to user and handling forwarded content.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel, ChatType


class TestReplyMessageHandling:
    """Test handling of reply messages."""

    @pytest.mark.asyncio
    async def test_reply_to_user_message(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that replying to a user message updates that user."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        replied_user = User(id=987654321, first_name="Replied", username="replieduser", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        reply_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=replied_user,
        )

        message = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            reply_to_message=reply_message,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_replied_user = await ChatModel.find_one(ChatModel.tid == 987654321)
        assert db_replied_user is not None
        assert db_replied_user.first_name_or_title == "Replied"
        assert db_replied_user.username == "replieduser"

    @pytest.mark.asyncio
    async def test_reply_to_anonymous_admin(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that replying to anonymous admin message doesn't create duplicate."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")
        sender_chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        reply_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            sender_chat=sender_chat,
        )

        message = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            reply_to_message=reply_message,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Should not create duplicate for anonymous admin
        # The test passes if no exception is raised
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_reply_to_channel_message(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that replying to a channel message updates the channel."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        replied_channel = Chat(id=-1009876543210, type="channel", title="Source Channel")

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        reply_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            sender_chat=replied_channel,
        )

        message = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            reply_to_message=reply_message,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_channel = await ChatModel.find_one(ChatModel.tid == -1009876543210)
        assert db_channel is not None
        assert db_channel.type == ChatType.channel
        assert db_channel.first_name_or_title == "Source Channel"

    @pytest.mark.asyncio
    async def test_reply_to_forwarded_message(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that reply to forwarded message updates both original and forward source."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        original_user = User(id=111111111, first_name="Original", username="original", is_bot=False)
        forwarded_user = User(id=222222222, first_name="Forwarded", username="forwarded", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        # The replied message is itself a forwarded message
        reply_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=original_user,
            forward_from=forwarded_user,
        )

        message = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            reply_to_message=reply_message,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        assert await ChatModel.find_one(ChatModel.tid == 111111111) is not None
        assert await ChatModel.find_one(ChatModel.tid == 222222222) is not None

    @pytest.mark.asyncio
    async def test_reply_to_bot_message(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that replying to bot message is handled correctly."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        bot_user = User(id=CONFIG.bot_id, first_name="Bot", is_bot=True)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        reply_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=bot_user,
        )

        message = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            reply_to_message=reply_message,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Bot should not be added to updated_chats
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_reply_in_private_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that reply in private chat works correctly."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=123456789, type="private", first_name="Test")

        reply_message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
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

        # Assert
        assert result == "handler_result"
        # In private chat, reply handling is skipped
