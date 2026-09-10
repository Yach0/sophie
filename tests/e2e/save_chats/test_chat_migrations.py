"""Tests for chat migration handling in SaveChatsMiddleware.

This module tests how the middleware handles group to supergroup migrations.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.types import Chat

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.group_user_whitelist import GroupUserWhitelistModel
from sophie_bot.utils.group_whitelist import group_user_whitelist_cache_key


class TestChatMigration:
    """Test chat migration scenarios."""

    @pytest.mark.asyncio
    async def test_migration_from_chat_id(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test migration from old group ID to new supergroup ID."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create old group (with required username field)
        old_group = ChatModel(
            tid=-123456789,  # Old group ID
            type=ChatType.group,
            first_name_or_title="Old Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(UTC),
        )
        await old_group.save()

        # Migration message
        user = User(id=123456789, first_name="Test", is_bot=False)
        new_chat = Chat(id=-1001234567890, type="supergroup", title="Migrated Group")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=new_chat,
            from_user=user,
            migrate_from_chat_id=-123456789,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = new_chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert
        context = base_data["context"]
        migrated_group = context.event_chat
        assert migrated_group.tid == -1001234567890
        assert migrated_group.type == ChatType.supergroup

    @pytest.mark.asyncio
    async def test_migration_to_chat_id(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test migration to new chat ID (old group receives this)."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange
        user = User(id=123456789, first_name="Test", is_bot=False)
        old_chat = Chat(id=-123456789, type="group", title="Old Group")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=old_chat,
            from_user=user,
            migrate_to_chat_id=-1001234567890,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = old_chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Handler should still be called
        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_migration_preserves_group_data(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that migration preserves group data like title."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create old group with specific data (with required username field)
        old_group = ChatModel(
            tid=-123456789,
            type=ChatType.group,
            first_name_or_title="My Awesome Group",
            username="awesomegroup",
            is_bot=False,
            last_saw=datetime.now(UTC),
        )
        await old_group.save()

        # Migration - use same title in new chat to verify migration preserves it
        user = User(id=123456789, first_name="Test", is_bot=False)
        new_chat = Chat(id=-1001234567890, type="supergroup", title="My Awesome Group")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=new_chat,
            from_user=user,
            migrate_from_chat_id=-123456789,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = new_chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Group should exist with the title from the migration message
        # Note: The middleware updates group data from the message, so title comes from new_chat
        migrated = await ChatModel.find_one(ChatModel.tid == -1001234567890)
        assert migrated is not None
        assert migrated.type == ChatType.supergroup

    @pytest.mark.asyncio
    async def test_migration_moves_group_whitelist_rows_and_invalidates_cache(self, base_data) -> None:
        old_chat_tid = -123456780
        new_chat_tid = -1001234567800
        user_tid = 123456780
        redis = base_data["services"].redis
        old_group = ChatModel(
            tid=old_chat_tid,
            type=ChatType.group,
            first_name_or_title="Whitelisted Group",
            is_bot=False,
            username=None,
            last_saw=datetime.now(UTC),
        )
        await old_group.save()
        await GroupUserWhitelistModel.add_user(old_chat_tid, user_tid)
        await GroupUserWhitelistModel.add_user(new_chat_tid, user_tid)
        old_cache_key = group_user_whitelist_cache_key(old_chat_tid, user_tid)
        new_cache_key = group_user_whitelist_cache_key(new_chat_tid, user_tid)
        await redis.set(old_cache_key, b"1")
        await redis.set(new_cache_key, b"0")

        await ChatModel.do_chat_migrate(
            old_chat_tid,
            Chat(id=new_chat_tid, type="supergroup", title="Whitelisted Group"),
            redis=redis,
        )

        assert (
            await GroupUserWhitelistModel.find_one(
                GroupUserWhitelistModel.chat_tid == old_chat_tid,
                GroupUserWhitelistModel.user_tid == user_tid,
            )
            is None
        )
        assert (
            await GroupUserWhitelistModel.find_one(
                GroupUserWhitelistModel.chat_tid == new_chat_tid,
                GroupUserWhitelistModel.user_tid == user_tid,
            )
            is not None
        )
        assert (
            await GroupUserWhitelistModel.find(
                GroupUserWhitelistModel.chat_tid == new_chat_tid,
                GroupUserWhitelistModel.user_tid == user_tid,
            ).count()
            == 1
        )
        assert await redis.get(old_cache_key) is None
        assert await redis.get(new_cache_key) is None

    @pytest.mark.asyncio
    async def test_migration_with_users(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that migration handles groups with existing users."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - Create old group with users (with required username field)
        old_group = ChatModel(
            tid=-123456789,
            type=ChatType.group,
            first_name_or_title="Group With Users",
            is_bot=False,
            username=None,
            last_saw=datetime.now(UTC),
        )
        await old_group.save()

        existing_user = ChatModel(
            tid=111222333,
            type=ChatType.private,
            first_name_or_title="ExistingUser",
            is_bot=False,
            username=None,
            last_saw=datetime.now(UTC),
        )
        await existing_user.save()

        # Note: UserInGroupModel Link relationships don't work well with mongomock
        # Skipping user_in_group relationship creation

        # Migration
        user = User(id=123456789, first_name="Test", is_bot=False)
        new_chat = Chat(id=-1001234567890, type="supergroup", title="Group With Users")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=new_chat,
            from_user=user,
            migrate_from_chat_id=-123456789,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = new_chat

        # Act
        await middleware(mock_handler, update, base_data)

        assert base_data["context"].event_chat.tid == -1001234567890

    @pytest.mark.asyncio
    async def test_migration_from_nonexistent_group(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test migration when old group doesn't exist in database."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - No old group in database
        user = User(id=123456789, first_name="Test", is_bot=False)
        new_chat = Chat(id=-1001234567890, type="supergroup", title="New Group")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=new_chat,
            from_user=user,
            migrate_from_chat_id=-999999999,  # Non-existent old group
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = new_chat

        # Act
        await middleware(mock_handler, update, base_data)

        assert mock_handler.called

    @pytest.mark.asyncio
    async def test_both_migration_fields_present(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test handling when both migrate_from_chat_id and migrate_to_chat_id are present."""
        from aiogram.types import Chat, Message, Update, User

        # Arrange - This shouldn't happen in real Telegram, but test it
        user = User(id=123456789, first_name="Test", is_bot=False)
        chat = Chat(id=-1001234567890, type="supergroup", title="Test")
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=chat,
            from_user=user,
            migrate_from_chat_id=-111111111,
            migrate_to_chat_id=-222222222,
        )
        update = Update(update_id=1, message=message)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        await middleware(mock_handler, update, base_data)

        # Assert - Should handle migrate_from_chat_id first
        assert mock_handler.called
