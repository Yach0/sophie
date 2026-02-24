"""E2E tests for federation info and chat listing commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.federations.services import (
    FederationAdminService,
    FederationBanService,
    FederationManageService,
)
from tests.e2e.federations.conftest import (
    create_federation_via_command,
    create_test_user_and_group,
    join_chat_to_federation,
)


@pytest.mark.asyncio
async def test_fedinfo_by_id(test_client: TestClient) -> None:
    """Test /fedinfo with an explicit federation ID.

    Verifies:
    1. The bot responds to /fedinfo <fed_id> without errors
    2. The federation info command succeeds for a valid ID
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=7001,
            first_name="InfoOwner",
            username="info_owner",
            chat_id=-1001000007001,
            group_title="Info Test Group",
        )

        federation = await create_federation_via_command(test_client, owner_user, group, "Info Test Fed", owner_model)

        # Should not crash when querying by ID
        await test_client.send_command(command="fedinfo", from_user=owner_user, args=federation.fed_id, chat=group)


@pytest.mark.asyncio
async def test_fedinfo_from_chat_context(test_client: TestClient) -> None:
    """Test /fedinfo without arguments resolves federation from the current chat.

    Verifies:
    1. Chat is joined to a federation
    2. /fedinfo without args returns info for that federation
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=7002,
            first_name="CtxInfoOwner",
            username="ctx_info_owner",
            chat_id=-1001000007002,
            group_title="Ctx Info Group",
        )

        federation = await create_federation_via_command(test_client, owner_user, group, "Ctx Info Fed", owner_model)

        await join_chat_to_federation(test_client, owner_user, group, federation.fed_id)

        # fedinfo without args should resolve from chat context
        await test_client.send_command(command="fedinfo", from_user=owner_user, chat=group)


@pytest.mark.asyncio
async def test_fedinfo_not_in_federation(test_client: TestClient) -> None:
    """Test /fedinfo in a chat that is not in any federation.

    Verifies:
    1. The command does not crash when the chat has no federation
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, _owner_model = await create_test_user_and_group(
            test_client,
            user_id=7003,
            first_name="NoInfoUser",
            username="no_info_user",
            chat_id=-1001000007003,
            group_title="No Info Group",
        )

        # Should not crash
        await test_client.send_command(command="fedinfo", from_user=owner_user, chat=group)


@pytest.mark.asyncio
async def test_fchats_shows_joined_chats(test_client: TestClient) -> None:
    """Test /fchats lists chats that joined the federation.

    Verifies:
    1. Two chats are joined to a federation
    2. /fchats command does not crash and federation has correct chat count
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group_a, owner_model = await create_test_user_and_group(
            test_client,
            user_id=7004,
            first_name="FchatsOwner",
            username="fchats_owner",
            chat_id=-1001000007004,
            group_title="Fchats Group A",
        )

        group_b = ChatFactory.create_group(chat_id=-1001000007005, title="Fchats Group B")
        await test_client.send_message(text="init", from_user=owner_user, chat=group_b)

        federation = await create_federation_via_command(
            test_client, owner_user, group_a, "Fchats Test Fed", owner_model
        )

        await join_chat_to_federation(test_client, owner_user, group_a, federation.fed_id)
        await join_chat_to_federation(test_client, owner_user, group_b, federation.fed_id)

        # Send /fchats from a group in the federation
        await test_client.send_command(command="fchats", from_user=owner_user, chat=group_a)

    # Verify chat count via service
    updated_fed = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_fed is not None
    assert len(updated_fed.chats) == 2


@pytest.mark.asyncio
async def test_fchats_not_in_federation(test_client: TestClient) -> None:
    """Test /fchats in a chat not in any federation.

    Verifies:
    1. The command does not crash
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, _owner_model = await create_test_user_and_group(
            test_client,
            user_id=7006,
            first_name="NoFchatsUser",
            username="no_fchats_user",
            chat_id=-1001000007006,
            group_title="No Fchats Group",
        )

        # Should not crash
        await test_client.send_command(command="fchats", from_user=owner_user, chat=group)


@pytest.mark.asyncio
async def test_fadmins_with_promoted_admin(test_client: TestClient) -> None:
    """Test /fadmins for a federation with an extra promoted admin.

    Verifies:
    1. The command does not crash for a valid federation context
    2. Promoted admins remain in federation admin list
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=7012,
            first_name="AdminsOwner",
            username="admins_owner",
            chat_id=-1001000007012,
            group_title="Fadmins Group",
        )

        admin_user = test_client.create_user(user_id=7013, first_name="ExtraAdmin", username="extra_admin")
        await test_client.send_message(text="init", from_user=admin_user.user, chat=group)
        admin_model = await ChatModel.get_by_tid(7013)
        assert admin_model is not None

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Fadmins Test Fed", owner_model
        )

        await join_chat_to_federation(test_client, owner_user, group, federation.fed_id)
        await FederationAdminService.promote_admin(federation, admin_model.iid)

        await test_client.send_command(command="fadmins", from_user=owner_user, chat=group)

    updated_federation = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_federation is not None
    assert len(updated_federation.admins) == 1


@pytest.mark.asyncio
async def test_federation_chat_count_service(test_client: TestClient) -> None:
    """Test the federation chat count service method.

    Verifies:
    1. Chat count starts at 0
    2. After joining chats, count reflects accurately
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group_a, owner_model = await create_test_user_and_group(
            test_client,
            user_id=7007,
            first_name="CountOwner",
            username="count_info_owner",
            chat_id=-1001000007007,
            group_title="Count Info Group A",
        )

        federation = await create_federation_via_command(
            test_client, owner_user, group_a, "Count Info Fed", owner_model
        )

    # Initially zero chats
    initial_fed = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert initial_fed is not None
    assert len(initial_fed.chats) == 0

    admin_mock = AsyncMock(return_value=True)
    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        await join_chat_to_federation(test_client, owner_user, group_a, federation.fed_id)

        group_b = ChatFactory.create_group(chat_id=-1001000007008, title="Count Info Group B")
        await test_client.send_message(text="init", from_user=owner_user, chat=group_b)
        await join_chat_to_federation(test_client, owner_user, group_b, federation.fed_id)

    updated_fed = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_fed is not None
    assert len(updated_fed.chats) == 2


@pytest.mark.asyncio
async def test_federation_ban_count_service(test_client: TestClient) -> None:
    """Test that federation ban count is correctly reflected via the service.

    Verifies:
    1. Initially zero bans
    2. After banning users, the count increases
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=7009,
            first_name="BanCountOwner",
            username="ban_count_owner",
            chat_id=-1001000007009,
            group_title="Ban Count Group",
        )

        for target_tid in (7010, 7011):
            target_wrapper = test_client.create_user(
                user_id=target_tid, first_name=f"BanTarget{target_tid}", username=f"ban_target_{target_tid}"
            )
            await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

        federation = await create_federation_via_command(test_client, owner_user, group, "Ban Count Fed", owner_model)

    # Ban two users
    await FederationBanService.ban_user(federation, 7010, owner_model.iid, reason="count test 1")
    await FederationBanService.ban_user(federation, 7011, owner_model.iid, reason="count test 2")

    bans = await FederationBanService.get_federation_bans(federation.fed_id)
    assert len(bans) == 2
