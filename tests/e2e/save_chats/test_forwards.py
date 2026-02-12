"""Tests for forwarded message handling in SaveChatsMiddleware.

This module tests how the middleware handles forwarded messages from users and chats.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel


class TestForwardedMessageHandling:
    """Test handling of forwarded messages."""

    @pytest.mark.asyncio
    async def test_forward_from_user(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that forwarding from user updates that user."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        forwarded_user = User(id=555666777, first_name="Forwarded", username="forwarded", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            forward_from=forwarded_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_forwarded = await ChatModel.find_one(ChatModel.tid == 555666777)
        assert db_forwarded is not None
        assert db_forwarded.first_name_or_title == "Forwarded"

    @pytest.mark.asyncio
    async def test_forward_from_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that forwarding from chat updates that chat."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        forwarded_chat = Chat(id=-100999888777, type="channel", title="Forwarded Channel")

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            forward_from_chat=forwarded_chat,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_forwarded = await ChatModel.find_one(ChatModel.tid == -100999888777)
        assert db_forwarded is not None
        assert db_forwarded.first_name_or_title == "Forwarded Channel"

    @pytest.mark.asyncio
    async def test_forward_from_same_group_ignored(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that forwarding from the same group is ignored."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")
        same_chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            forward_from_chat=same_chat,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Should not create duplicate
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_forward_from_bot_ignored(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that forwarding from bot is not tracked."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        bot_user = User(id=CONFIG.bot_id, first_name="Bot", is_bot=True)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            forward_from=bot_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Bot should not be in updated_chats
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_forward_both_user_and_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test message with both forward_from and forward_from_chat."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        forwarded_user = User(id=555666777, first_name="ForwardedUser", is_bot=False)
        forwarded_chat = Chat(id=-100999888777, type="channel", title="ForwardedChannel")

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        # This shouldn't happen in real Telegram, but test it
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            forward_from=forwarded_user,
            forward_from_chat=forwarded_chat,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Chat takes precedence
        db_chat = await ChatModel.find_one(ChatModel.tid == -100999888777)
        assert db_chat is not None

    @pytest.mark.asyncio
    async def test_forward_in_private_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that forwarded message in private chat is handled."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        forwarded_user = User(id=555666777, first_name="Forwarded", is_bot=False)

        chat = Chat(id=123456789, type="private", first_name="Test")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            forward_from=forwarded_user,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"
        # In private chat, forward handling is skipped

    @pytest.mark.asyncio
    async def test_forward_from_channel_with_username(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test forwarding from channel with public username."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        main_user = User(id=123456789, first_name="Main", is_bot=False)
        forwarded_chat = Chat(id=-100999888777, type="channel", title="Public Channel", username="publicchannel")

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=main_user,
            forward_from_chat=forwarded_chat,
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = main_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_channel = await ChatModel.find_one(ChatModel.tid == -100999888777)
        assert db_channel is not None
        assert db_channel.username == "publicchannel"
