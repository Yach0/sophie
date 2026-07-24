"""Shared fixtures and utilities for SaveChatsMiddleware tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.types import Chat, ChatMember, ChatMemberUpdated, Message, Update, User

from sophie_bot.middlewares.save_chats import SaveChatsMiddleware


@pytest_asyncio.fixture
async def middleware() -> SaveChatsMiddleware:
    """Create a SaveChatsMiddleware instance."""
    return SaveChatsMiddleware()


@pytest_asyncio.fixture
async def mock_handler() -> AsyncMock:
    """Create a mock handler for middleware testing."""
    return AsyncMock(return_value="handler_result")


@pytest_asyncio.fixture
async def base_data() -> dict[str, Any]:
    """Create base data dictionary for middleware."""
    return {
        "event_from_user": None,
        "event_chat": None,
    }


class TestDataFactory:
    """Factory for creating test data objects."""

    @staticmethod
    def create_user(
        user_id: int = 123456789,
        first_name: str = "Test",
        username: str | None = "testuser",
        is_bot: bool = False,
    ) -> User:
        """Create a test user."""
        return User(
            id=user_id,
            first_name=first_name,
            username=username,
            is_bot=is_bot,
        )

    @staticmethod
    def create_private_chat(
        chat_id: int = 123456789,
        first_name: str = "Test",
        username: str | None = "testuser",
    ) -> Chat:
        """Create a private chat."""
        return Chat(
            id=chat_id,
            type="private",
            first_name=first_name,
            username=username,
        )

    @staticmethod
    def create_group_chat(
        chat_id: int = -1001234567890,
        title: str = "Test Group",
        chat_type: str = "supergroup",
        is_forum: bool = False,
    ) -> Chat:
        """Create a group chat."""
        return Chat(
            id=chat_id,
            type=chat_type,
            title=title,
            is_forum=is_forum,
        )

    @staticmethod
    def create_message(
        chat: Chat,
        from_user: User | None = None,
        message_id: int = 1,
        **kwargs: Any,
    ) -> Message:
        """Create a test message."""
        return Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=chat,
            from_user=from_user,
            **kwargs,
        )

    @staticmethod
    def create_update(message: Message | None = None, **kwargs: Any) -> Update:
        """Create a test update."""
        return Update(update_id=1, message=message, **kwargs)

    @staticmethod
    def create_bot_user(bot_id: int, first_name: str = "Bot") -> User:
        """Create the bot user used in chat member updates."""
        return User(id=bot_id, first_name=first_name, is_bot=True)

    @staticmethod
    def create_my_chat_member_update(
        chat: Chat,
        from_user: User,
        old_member: ChatMember,
        new_member: ChatMember,
        update_id: int = 1,
    ) -> Update:
        """Create a my_chat_member update for SaveChatsMiddleware tests."""
        return Update(
            update_id=update_id,
            my_chat_member=ChatMemberUpdated(
                chat=chat,
                from_user=from_user,
                date=datetime.now(UTC),
                old_chat_member=old_member,
                new_chat_member=new_member,
            ),
        )
