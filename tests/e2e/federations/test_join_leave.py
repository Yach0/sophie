"""E2E tests for federation join/leave flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.federations.services import FederationManageService
from tests.e2e.federations.conftest import (
    create_federation_via_command,
    create_test_user_and_group,
    join_chat_to_federation,
)


@pytest.mark.asyncio
async def test_join_federation(test_client: TestClient) -> None:
    """Test joining a chat to a federation via /joinfed.

    Verifies:
    1. After /joinfed the chat appears in the federation's chats list
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=3001,
            first_name="JoinOwner",
            username="join_owner",
            chat_id=-1001000003001,
            group_title="Join Test Group",
        )

        federation = await create_federation_via_command(test_client, owner_user, group, "Join Test Fed", owner_model)

        await join_chat_to_federation(test_client, owner_user, group, federation.fed_id)

    updated_fed = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_fed is not None
    assert len(updated_fed.chats) == 1, "Group should be joined to the federation"


@pytest.mark.asyncio
async def test_join_federation_duplicate(test_client: TestClient) -> None:
    """Test that joining the same chat twice does not create a duplicate entry.

    Verifies:
    1. First /joinfed succeeds
    2. Second /joinfed to the same federation does not add a duplicate chat entry
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=3002,
            first_name="DupJoinOwner",
            username="dup_join_owner",
            chat_id=-1001000003002,
            group_title="Dup Join Group",
        )

        federation = await create_federation_via_command(test_client, owner_user, group, "Dup Join Fed", owner_model)

        await join_chat_to_federation(test_client, owner_user, group, federation.fed_id)
        await join_chat_to_federation(test_client, owner_user, group, federation.fed_id)

    updated_fed = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_fed is not None
    assert len(updated_fed.chats) == 1, "Chat should not be duplicated in federation"


@pytest.mark.asyncio
async def test_join_federation_already_in_another(test_client: TestClient) -> None:
    """Test that a chat already in one federation cannot join another.

    Verifies:
    1. Chat joins Federation A
    2. Chat tries to join Federation B - it remains in Federation A only
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_a, group, model_a = await create_test_user_and_group(
            test_client,
            user_id=3003,
            first_name="OwnerA",
            username="owner_a_join",
            chat_id=-1001000003003,
            group_title="Cross Join Group",
        )

        owner_b_wrapper = test_client.create_user(user_id=3004, first_name="OwnerB", username="owner_b_join")
        group_b = ChatFactory.create_group(chat_id=-1001000003004, title="OwnerB Group")
        await test_client.send_message(text="init", from_user=owner_b_wrapper.user, chat=group_b)
        model_b = await ChatModel.get_by_tid(3004)
        assert model_b is not None

        fed_a = await create_federation_via_command(test_client, owner_a, group, "Fed A Cross", model_a)
        fed_b = await create_federation_via_command(test_client, owner_b_wrapper.user, group_b, "Fed B Cross", model_b)

        # Join group to Fed A
        await join_chat_to_federation(test_client, owner_a, group, fed_a.fed_id)

        # Try to join the same group to Fed B
        await test_client.send_command(command="joinfed", from_user=owner_a, args=fed_b.fed_id, chat=group)

    updated_fed_a = await FederationManageService.get_federation_by_id(fed_a.fed_id)
    updated_fed_b = await FederationManageService.get_federation_by_id(fed_b.fed_id)
    assert updated_fed_a is not None
    assert updated_fed_b is not None
    assert len(updated_fed_a.chats) == 1, "Group should still be in Fed A"
    assert len(updated_fed_b.chats) == 0, "Group should NOT be added to Fed B"


@pytest.mark.asyncio
async def test_leave_federation(test_client: TestClient) -> None:
    """Test leaving a federation via /leavefed.

    Verifies:
    1. Chat joins a federation
    2. /leavefed removes the chat from federation
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=3005,
            first_name="LeaveOwner",
            username="leave_owner",
            chat_id=-1001000003005,
            group_title="Leave Test Group",
        )

        federation = await create_federation_via_command(test_client, owner_user, group, "Leave Test Fed", owner_model)

        await join_chat_to_federation(test_client, owner_user, group, federation.fed_id)

        # Verify joined
        fed_after_join = await FederationManageService.get_federation_by_id(federation.fed_id)
        assert fed_after_join is not None
        assert len(fed_after_join.chats) == 1

        # /leavefed command should be handled without errors
        await test_client.send_command(command="leavefed", from_user=owner_user, chat=group)

    updated_fed = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_fed is not None
    assert len(updated_fed.chats) == 0, "Chat should be removed from the federation"


@pytest.mark.asyncio
async def test_leave_federation_not_joined(test_client: TestClient) -> None:
    """Test that /leavefed in a chat not in any federation does not crash.

    Verifies:
    1. Sending /leavefed in a chat not joined to any federation doesn't raise errors
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, _owner_model = await create_test_user_and_group(
            test_client,
            user_id=3006,
            first_name="NoFedUser",
            username="no_fed_user",
            chat_id=-1001000003006,
            group_title="No Fed Group",
        )

        # Should not crash
        await test_client.send_command(command="leavefed", from_user=owner_user, chat=group)


@pytest.mark.asyncio
async def test_multiple_chats_join_same_federation(test_client: TestClient) -> None:
    """Test that multiple different chats can join the same federation.

    Verifies:
    1. Two different groups join the same federation
    2. The federation's chats list contains both groups
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group_a, owner_model = await create_test_user_and_group(
            test_client,
            user_id=3007,
            first_name="MultiOwner",
            username="multi_owner",
            chat_id=-1001000003007,
            group_title="Multi Group A",
        )

        group_b = ChatFactory.create_group(chat_id=-1001000003008, title="Multi Group B")
        await test_client.send_message(text="init", from_user=owner_user, chat=group_b)

        federation = await create_federation_via_command(
            test_client, owner_user, group_a, "Multi Chat Fed", owner_model
        )

        await join_chat_to_federation(test_client, owner_user, group_a, federation.fed_id)
        await join_chat_to_federation(test_client, owner_user, group_b, federation.fed_id)

    updated_fed = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_fed is not None
    assert len(updated_fed.chats) == 2, "Both groups should be in the federation"
