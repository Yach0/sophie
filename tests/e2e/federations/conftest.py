"""Shared fixtures and helpers for federation e2e tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation

if TYPE_CHECKING:
    from aiogram.types import Chat, User


class FederationTestContext:
    """Holds references to users, groups, and the admin mock for a federation test."""

    def __init__(
        self,
        owner_user: User,
        owner_model: ChatModel,
        group: Chat,
        admin_mock: AsyncMock,
    ) -> None:
        self.owner_user = owner_user
        self.owner_model = owner_model
        self.group = group
        self.admin_mock = admin_mock


async def create_test_user_and_group(
    test_client: TestClient,
    user_id: int,
    first_name: str,
    username: str,
    chat_id: int,
    group_title: str,
) -> tuple[User, Chat, ChatModel]:
    """Create a test user and group, and trigger SaveChatsMiddleware for both."""
    test_user_wrapper = test_client.create_user(user_id=user_id, first_name=first_name, username=username)
    group = ChatFactory.create_group(chat_id=chat_id, title=group_title)
    # Send a message to trigger SaveChatsMiddleware so ChatModel records exist
    await test_client.send_message(text="init", from_user=test_user_wrapper.user, chat=group)

    user_model = await ChatModel.get_by_tid(user_id)
    assert user_model is not None, f"ChatModel for user {user_id} should exist after init message"

    return test_user_wrapper.user, group, user_model


async def create_federation_via_command(
    test_client: TestClient,
    owner_user: User,
    group: Chat,
    fed_name: str,
    owner_model: ChatModel,
) -> Federation:
    """Create a federation via the /newfed command and return it.

    Note: We look up by ``fed_name`` because mongomock does not support
    ``$id`` sub-queries on DBRef fields, so ``get_federation_by_creator``
    always returns ``None`` in the test environment.
    """
    await test_client.send_command(command="newfed", from_user=owner_user, args=fed_name, chat=group)
    federation = await Federation.find_one(Federation.fed_name == fed_name)
    assert federation is not None, f"Federation '{fed_name}' should be created"
    assert federation.fed_name == fed_name
    return federation


async def join_chat_to_federation(
    test_client: TestClient,
    user: User,
    group: Chat,
    fed_id: str,
) -> None:
    """Join a chat to a federation via the /joinfed command."""
    await test_client.send_command(command="joinfed", from_user=user, args=fed_id, chat=group)


@pytest_asyncio.fixture
async def fed_context(test_client: TestClient) -> FederationTestContext:
    """Create a standard federation test context with one owner, one group, and a created federation.

    The admin mock is active for the lifetime of the fixture.
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=5000,
            first_name="FedOwner",
            username="fed_owner",
            chat_id=-1001000005000,
            group_title="Fed Test Group",
        )

    ctx = FederationTestContext(
        owner_user=owner_user,
        owner_model=owner_model,
        group=group,
        admin_mock=admin_mock,
    )
    return ctx
