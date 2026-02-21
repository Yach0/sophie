"""E2E tests for federation ban/unban flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import FederationBan
from sophie_bot.modules.federations.exceptions import FederationBanValidationError
from sophie_bot.modules.federations.services import FederationBanService, FederationManageService
from tests.e2e.federations.conftest import (
    create_federation_via_command,
    create_test_user_and_group,
    join_chat_to_federation,
)


@pytest.mark.asyncio
async def test_fban_user_via_service(test_client: TestClient) -> None:
    """Test banning a user in a federation via the service layer.

    Verifies:
    1. A federation is created and a user is banned
    2. The ban record exists in the database with correct fields
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=4001,
            first_name="BanOwner",
            username="ban_owner",
            chat_id=-1001000004001,
            group_title="Ban Test Group",
        )

        target_wrapper = test_client.create_user(user_id=4002, first_name="Target", username="ban_target")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Ban Test Fed", owner_model
        )

    # Ban user via service
    ban = await FederationBanService.ban_user(federation, 4002, owner_model.iid, reason="test ban reason")
    assert ban is not None
    assert ban.user_id == 4002
    assert ban.fed_id == federation.fed_id
    assert ban.reason == "test ban reason"

    # Verify via lookup
    is_banned = await FederationBanService.is_user_banned(federation.fed_id, 4002)
    assert is_banned is not None, "User should be banned"


@pytest.mark.asyncio
async def test_fban_and_unfban_via_service(test_client: TestClient) -> None:
    """Test banning and unbanning a user via the service layer.

    Verifies:
    1. User is banned
    2. User is unbanned
    3. Ban record is removed from the database
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=4003,
            first_name="UnbanOwner",
            username="unban_owner",
            chat_id=-1001000004003,
            group_title="Unban Test Group",
        )

        target_wrapper = test_client.create_user(user_id=4004, first_name="UnbanTarget", username="unban_target")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Unban Test Fed", owner_model
        )

    # Ban user
    await FederationBanService.ban_user(federation, 4004, owner_model.iid, reason="temp ban")

    # Unban user
    success, origin_ban = await FederationBanService.unban_user(federation.fed_id, 4004)
    assert success is True, "Unban should succeed"
    assert origin_ban is None, "Should not be an origin-fed ban"

    # Verify unbanned
    is_banned = await FederationBanService.is_user_banned(federation.fed_id, 4004)
    assert is_banned is None, "User should no longer be banned"


@pytest.mark.asyncio
async def test_fban_updates_reason_on_reban(test_client: TestClient) -> None:
    """Test that banning an already-banned user updates the reason.

    Verifies:
    1. User is banned with reason A
    2. User is banned again with reason B
    3. The ban record now has reason B
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=4005,
            first_name="RebanOwner",
            username="reban_owner",
            chat_id=-1001000004005,
            group_title="Reban Test Group",
        )

        target_wrapper = test_client.create_user(user_id=4006, first_name="RebanTarget", username="reban_target")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Reban Test Fed", owner_model
        )

    # Ban user with first reason
    await FederationBanService.ban_user(federation, 4006, owner_model.iid, reason="first reason")

    # Ban again with updated reason
    updated_ban = await FederationBanService.ban_user(federation, 4006, owner_model.iid, reason="updated reason")
    assert updated_ban.reason == "updated reason"

    # Should still only have one ban record
    bans = await FederationBan.find(
        FederationBan.fed_id == federation.fed_id, FederationBan.user_id == 4006
    ).to_list()
    assert len(bans) == 1, "Should not create duplicate ban records"


@pytest.mark.asyncio
async def test_unfban_nonexistent_user(test_client: TestClient) -> None:
    """Test that unbanning a user who is not banned returns failure.

    Verifies:
    1. Unbanning a non-banned user returns (False, None)
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=4007,
            first_name="NoUnbanOwner",
            username="no_unban_owner",
            chat_id=-1001000004007,
            group_title="No Unban Group",
        )

        federation = await create_federation_via_command(
            test_client, owner_user, group, "No Unban Fed", owner_model
        )

    success, origin_ban = await FederationBanService.unban_user(federation.fed_id, 99999)
    assert success is False, "Unbanning a non-banned user should return False"
    assert origin_ban is None


@pytest.mark.asyncio
async def test_fban_cannot_ban_federation_owner(test_client: TestClient) -> None:
    """Test that the federation owner cannot be banned.

    Verifies:
    1. Attempting to ban the federation owner raises a validation error
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=4008,
            first_name="SelfBanOwner",
            username="self_ban_owner",
            chat_id=-1001000004008,
            group_title="Self Ban Group",
        )

        # Create a second user to attempt the ban
        admin_wrapper = test_client.create_user(user_id=4009, first_name="Admin", username="fed_admin")
        await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)
        admin_model = await ChatModel.get_by_tid(4009)
        assert admin_model is not None

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Self Ban Fed", owner_model
        )

    # Try to ban the federation owner
    with pytest.raises(FederationBanValidationError, match="Cannot ban the federation owner"):
        await FederationBanService.ban_user(federation, 4008, admin_model.iid)


@pytest.mark.asyncio
async def test_fban_cannot_ban_self(test_client: TestClient) -> None:
    """Test that a user cannot ban themselves.

    Verifies:
    1. Attempting to ban yourself raises a validation error
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=4010,
            first_name="SelfBanner",
            username="self_banner",
            chat_id=-1001000004010,
            group_title="Self Ban Group 2",
        )

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Self Ban Fed 2", owner_model
        )

    with pytest.raises(FederationBanValidationError, match="You cannot ban yourself"):
        await FederationBanService.ban_user(federation, 4010, owner_model.iid)


@pytest.mark.asyncio
async def test_ban_count_tracking(test_client: TestClient) -> None:
    """Test that the federation ban count is tracked correctly.

    Verifies:
    1. Ban count increases after banning users
    2. Ban count decreases after unbanning
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=4011,
            first_name="CountOwner",
            username="count_owner",
            chat_id=-1001000004011,
            group_title="Count Test Group",
        )

        for target_tid in (4012, 4013, 4014):
            target_wrapper = test_client.create_user(
                user_id=target_tid, first_name=f"Target{target_tid}", username=f"target_{target_tid}"
            )
            await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Count Test Fed", owner_model
        )

    # Ban three users
    await FederationBanService.ban_user(federation, 4012, owner_model.iid, reason="ban 1")
    await FederationBanService.ban_user(federation, 4013, owner_model.iid, reason="ban 2")
    await FederationBanService.ban_user(federation, 4014, owner_model.iid, reason="ban 3")

    bans = await FederationBanService.get_federation_bans(federation.fed_id)
    assert len(bans) == 3, "Should have 3 bans"

    # Unban one
    await FederationBanService.unban_user(federation.fed_id, 4013)

    bans_after = await FederationBanService.get_federation_bans(federation.fed_id)
    assert len(bans_after) == 2, "Should have 2 bans after unbanning one"


@pytest.mark.asyncio
async def test_ban_in_subscription_chain(test_client: TestClient) -> None:
    """Test that bans are checked across the federation subscription chain.

    Verifies:
    1. Fed A subscribes to Fed B
    2. User is banned in Fed B
    3. User is detected as banned in Fed A's chain
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        user_a, group_a, model_a = await create_test_user_and_group(
            test_client,
            user_id=4020,
            first_name="ChainOwnerA",
            username="chain_owner_a",
            chat_id=-1001000004020,
            group_title="Chain Group A",
        )
        user_b, group_b, model_b = await create_test_user_and_group(
            test_client,
            user_id=4021,
            first_name="ChainOwnerB",
            username="chain_owner_b",
            chat_id=-1001000004021,
            group_title="Chain Group B",
        )

        target_wrapper = test_client.create_user(user_id=4022, first_name="ChainTarget", username="chain_target")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_a)

        fed_a = await create_federation_via_command(test_client, user_a, group_a, "Chain Fed A", model_a)
        fed_b = await create_federation_via_command(test_client, user_b, group_b, "Chain Fed B", model_b)

        # Join chats to their federations
        await join_chat_to_federation(test_client, user_a, group_a, fed_a.fed_id)
        await join_chat_to_federation(test_client, user_b, group_b, fed_b.fed_id)

    # Subscribe Fed A to Fed B via service (the /fsub command relies on
    # chat-context federation lookup which has DBRef issues in mongomock)
    success = await FederationManageService.subscribe_to_federation(fed_a, fed_b.fed_id)
    assert success is True, "Subscription should succeed"

    # Ban user in Fed B
    await FederationBanService.ban_user(fed_b, 4022, model_b.iid, reason="chain ban")

    # Check that Fed A's chain detects the ban
    result = await FederationBanService.is_user_banned_in_chain(fed_a.fed_id, 4022)
    assert result is not None, "User should be detected as banned via subscription chain"
    ban, banning_fed = result
    assert ban.user_id == 4022
    assert banning_fed.fed_id == fed_b.fed_id
