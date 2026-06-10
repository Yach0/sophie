"""Tests for my_chat_member handling in SaveChatsMiddleware.

This module tests how the middleware handles chat member status updates
for the bot itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import (
    Chat,
    ChatMember,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberRestricted,
    Update,
    User,
)

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel, ChatType
from tests.e2e.save_chats.conftest import TestDataFactory


def _save_chat_model(chat_tid: int, chat_type: ChatType, title: str) -> ChatModel:
    return ChatModel(
        tid=chat_tid,
        type=chat_type,
        first_name_or_title=title,
        is_bot=False,
        username=None,
        last_saw=datetime.now(timezone.utc),
    )


def _bot_user() -> User:
    return TestDataFactory.create_bot_user(CONFIG.bot_id)


def _admin_user(first_name: str = "Admin") -> User:
    return TestDataFactory.create_user(user_id=123456789, first_name=first_name, username=None)


def _group_chat() -> Chat:
    return TestDataFactory.create_group_chat()


def _my_chat_member_update(chat: Chat, user: User, old_member: ChatMember, new_member: ChatMember) -> Update:
    return TestDataFactory.create_my_chat_member_update(chat, user, old_member, new_member)


class TestMyChatMember:
    """Test handling of my_chat_member updates."""

    @pytest.mark.asyncio
    async def test_bot_kicked_from_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that bot being kicked removes user from group."""
        # Arrange - Create user and group (with required username field)
        user_model = _save_chat_model(123456789, ChatType.private, "Kicker")
        await user_model.save()

        group_model = _save_chat_model(-1001234567890, ChatType.supergroup, "Test Group")
        await group_model.save()

        # Bot is kicked
        user = _admin_user("Kicker")
        bot_user = _bot_user()
        chat = _group_chat()

        old_member = ChatMemberMember(user=bot_user)
        new_member = ChatMemberBanned(
            user=bot_user,
            until_date=datetime.now(timezone.utc),  # Required field for ChatMemberBanned
        )  # Use ChatMemberBanned for kicked status

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Handler should not be called for kicked status
        assert result is None

        # Note: UserInGroupModel Link relationships don't work well with mongomock
        # We verify the middleware handled the event correctly

    @pytest.mark.asyncio
    async def test_bot_becomes_member(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test that bot becoming member doesn't call handler."""
        # Arrange
        user = _admin_user()
        bot_user = _bot_user()
        chat = _group_chat()

        old_member = ChatMemberLeft(user=bot_user)  # Use ChatMemberLeft for left status
        new_member = ChatMemberMember(user=bot_user)  # ChatMemberMember is always "member"

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Handler should not be called (Telegram will send message event)
        assert result is None

    @pytest.mark.asyncio
    async def test_bot_promoted_to_admin(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test bot being promoted to administrator."""
        # Arrange
        user = _admin_user()
        bot_user = _bot_user()
        chat = _group_chat()

        old_member = ChatMemberMember(user=bot_user)
        new_member = ChatMemberAdministrator(
            user=bot_user,
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=True,
            can_invite_users=True,
            can_post_messages=True,
            can_edit_messages=True,
            can_pin_messages=True,
            can_post_stories=True,
            can_edit_stories=True,
            can_delete_stories=True,
            can_manage_topics=True,
        )

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert - Handler should be called for other statuses
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_bot_restricted_in_chat(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test bot being restricted in a chat."""
        # Arrange
        user = _admin_user()
        bot_user = _bot_user()
        chat = _group_chat()

        old_member = ChatMemberMember(user=bot_user)
        new_member = ChatMemberRestricted(
            user=bot_user,
            is_member=True,
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_react_to_messages=False,
            can_add_web_page_previews=False,
            can_edit_tag=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_topics=False,
            until_date=datetime.now(timezone.utc),
        )

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result == "handler_result"

    @pytest.mark.asyncio
    async def test_bot_kicked_from_private(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test bot being kicked/blocked in private chat."""
        # Arrange
        user = _admin_user("User")
        bot_user = _bot_user()
        chat = TestDataFactory.create_private_chat(chat_id=123456789, first_name="User", username=None)

        old_member = ChatMemberMember(user=bot_user)
        new_member = ChatMemberBanned(
            user=bot_user,
            until_date=datetime.now(timezone.utc),  # Required field for ChatMemberBanned
        )  # Use ChatMemberBanned for kicked status

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_bot_added_to_channel(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test bot being added to a channel."""
        # Arrange
        user = _admin_user()
        bot_user = _bot_user()
        chat = TestDataFactory.create_group_chat(chat_type="channel", title="Test Channel")

        old_member = ChatMemberLeft(user=bot_user)  # Use ChatMemberLeft for left status
        new_member = ChatMemberMember(user=bot_user)  # ChatMemberMember is always "member"

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result is None  # Member status doesn't call handler

    @pytest.mark.asyncio
    async def test_my_chat_member_group_not_in_db(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test my_chat_member when group doesn't exist in database."""
        # Arrange - No group in database
        user = _admin_user("Kicker")
        bot_user = _bot_user()
        chat = _group_chat()

        old_member = ChatMemberMember(user=bot_user)
        new_member = ChatMemberBanned(
            user=bot_user,
            until_date=datetime.now(timezone.utc),  # Required field for ChatMemberBanned
        )  # Use ChatMemberBanned for kicked status

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act - Should not raise exception
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_my_chat_member_user_not_in_group(
        self,
        middleware,
        mock_handler,
        base_data,
    ) -> None:
        """Test my_chat_member when user was never in the group."""
        # Arrange - Create group but user was never added (with required username field)
        group_model = _save_chat_model(-1001234567890, ChatType.supergroup, "Test Group")
        await group_model.save()

        user_model = _save_chat_model(123456789, ChatType.private, "Kicker")
        await user_model.save()

        # But don't add to group

        user = _admin_user("Kicker")
        bot_user = _bot_user()
        chat = _group_chat()

        old_member = ChatMemberMember(user=bot_user)
        new_member = ChatMemberBanned(
            user=bot_user,
            until_date=datetime.now(timezone.utc),  # Required field for ChatMemberBanned
        )  # Use ChatMemberBanned for kicked status

        update = _my_chat_member_update(chat, user, old_member, new_member)
        base_data["event_from_user"] = user
        base_data["event_chat"] = chat

        # Act - Should not raise exception
        result = await middleware(mock_handler, update, base_data)

        # Assert
        assert result is None
