"""Tests for new chat member handling in SaveChatsMiddleware.

This module tests how the middleware handles users joining groups.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sophie_bot.db.models.chat import ChatModel, ChatType


class TestNewChatMembers:
    """Test handling of new chat members."""

    @pytest.mark.asyncio
    async def test_new_chat_member(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that new chat member is added to database."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        new_member = User(id=111222333, first_name="NewMember", username="newmember", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            new_chat_members=[new_member],
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_new_member = await ChatModel.find_one(ChatModel.tid == 111222333)
        assert db_new_member is not None
        assert db_new_member.first_name_or_title == "NewMember"

        # Verify group was also created/updated
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_group is not None
        assert db_group.type == ChatType.supergroup

    @pytest.mark.asyncio
    async def test_multiple_new_chat_members(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that multiple new chat members are all added."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        new_member1 = User(id=111222333, first_name="Member1", is_bot=False)
        new_member2 = User(id=444555666, first_name="Member2", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            new_chat_members=[new_member1, new_member2],
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        assert await ChatModel.find_one(ChatModel.tid == 111222333) is not None
        assert await ChatModel.find_one(ChatModel.tid == 444555666) is not None

    @pytest.mark.asyncio
    async def test_new_member_already_exists_in_db(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test adding member who already exists in database."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create existing user
        existing_user = ChatModel(
            tid=111222333,
            type=ChatType.private,
            first_name_or_title="ExistingUser",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await existing_user.save()

        # Create group
        group = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group.save()

        # Now add existing user to group
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        new_member = User(id=111222333, first_name="ExistingUser", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            new_chat_members=[new_member],
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - User should be updated (we can't verify UserInGroupModel with mongomock)
        db_user = await ChatModel.find_one(ChatModel.tid == 111222333)
        assert db_user is not None
        assert db_user.first_name_or_title == "ExistingUser"

    @pytest.mark.asyncio
    async def test_new_member_who_is_already_in_group(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test adding member who is already in the group."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create user and group
        existing_user = ChatModel(
            tid=111222333,
            type=ChatType.private,
            first_name_or_title="ExistingUser",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await existing_user.save()

        group = ChatModel(
            tid=-1001234567890,
            type=ChatType.supergroup,
            first_name_or_title="Test Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(timezone.utc),
        )
        await group.save()

        # Try to add again
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        new_member = User(id=111222333, first_name="ExistingUser", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            new_chat_members=[new_member],
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - User and group should exist (we can't verify UserInGroupModel with mongomock)
        db_user = await ChatModel.find_one(ChatModel.tid == 111222333)
        db_group = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert db_user is not None
        assert db_group is not None

    @pytest.mark.asyncio
    async def test_bot_added_as_new_member(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that bot being added as member is handled."""
        from aiogram.types import Chat, Message, Update, User
        from sophie_bot.config import CONFIG

        # Arrange
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        bot_user = User(id=CONFIG.bot_id, first_name="Bot", is_bot=True)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            new_chat_members=[bot_user],
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Bot should be tracked
        db_bot = await ChatModel.find_one(ChatModel.tid == CONFIG.bot_id)
        assert db_bot is not None

    @pytest.mark.asyncio
    async def test_new_member_with_bot_adder(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test new member added by the bot itself."""
        from aiogram.types import Chat, Message, Update, User
        from sophie_bot.config import CONFIG

        # Arrange
        bot_user = User(id=CONFIG.bot_id, first_name="Bot", is_bot=True)
        new_member = User(id=111222333, first_name="NewMember", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=bot_user,
            new_chat_members=[new_member],
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = bot_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        db_new_member = await ChatModel.find_one(ChatModel.tid == 111222333)
        assert db_new_member is not None

    @pytest.mark.asyncio
    async def test_new_member_skip_already_updated(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that new member who was already updated in message handler is skipped."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        admin_user = User(id=123456789, first_name="Admin", is_bot=False)
        # Same user as admin (edge case)
        new_member = User(id=123456789, first_name="Admin", is_bot=False)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Group")

        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=admin_user,
            new_chat_members=[new_member],
        )

        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = admin_user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Should not create duplicate
        db_admin = await ChatModel.find_one(ChatModel.tid == 123456789)
        assert db_admin is not None
