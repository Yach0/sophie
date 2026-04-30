"""Shared fixtures and utilities for SaveChatsMiddleware tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.types import Chat, Message, Update, User

from tests.utils.db_fixture import (
    cleanup_beanie,
    create_mock_mongo,
    initialize_beanie,
    patch_pymongo,
)

# Create mock client and patch pymongo BEFORE any Beanie/model imports
_mock_mongo_client = create_mock_mongo()
_save_chats_patcher = patch_pymongo(_mock_mongo_client)
_save_chats_patcher.start()

# Now import models after patching
from sophie_bot.middlewares.save_chats import SaveChatsMiddleware  # noqa: E402


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


@pytest_asyncio.fixture(scope="session", autouse=True)
async def db_init():
    """Initialize Beanie with mocked MongoDB for all tests in this directory."""
    database = await initialize_beanie(_mock_mongo_client)

    yield database

    await cleanup_beanie()

    # Cleanup: stop the pymongo mock
    _save_chats_patcher.stop()


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
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=from_user,
            **kwargs,
        )

    @staticmethod
    def create_update(message: Message | None = None, **kwargs: Any) -> Update:
        """Create a test update."""
        return Update(update_id=1, message=message, **kwargs)
