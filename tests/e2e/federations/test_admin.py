"""E2E tests for federation admin promote/demote flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.federations.services import FederationAdminService
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from tests.e2e.federations.conftest import (
    create_federation_via_command,
    create_test_user_and_group,
)


@pytest.mark.asyncio
async def test_promote_admin_via_service(test_client: TestClient) -> None:
    """Test promoting a user to federation admin via the service layer.

    Verifies:
    1. A user is promoted to admin
    2. The user appears in the federation's admins list
    3. Permission checks recognize the user as admin
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=6001,
            first_name="PromoteOwner",
            username="promote_owner",
            chat_id=-1001000006001,
            group_title="Promote Test Group",
        )

        target_wrapper = test_client.create_user(user_id=6002, first_name="NewAdmin", username="new_admin")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)
        target_model = await ChatModel.get_by_tid(6002)
        assert target_model is not None

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Promote Test Fed", owner_model
        )

    # Promote user via service
    await FederationAdminService.promote_admin(federation, target_model.iid)

    # Verify admin status
    is_admin = await FederationAdminService.is_admin(federation, 6002)
    assert is_admin is True, "Promoted user should be recognized as admin"

    # Verify via permission service
    can_manage = await FederationPermissionService.can_manage_federation(federation, 6002)
    assert can_manage is True


@pytest.mark.asyncio
async def test_demote_admin_via_service(test_client: TestClient) -> None:
    """Test demoting a federation admin via the service layer.

    Verifies:
    1. A user is promoted then demoted
    2. The user is no longer an admin after demotion
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=6003,
            first_name="DemoteOwner",
            username="demote_owner",
            chat_id=-1001000006003,
            group_title="Demote Test Group",
        )

        target_wrapper = test_client.create_user(user_id=6004, first_name="DemotedAdmin", username="demoted_admin")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)
        target_model = await ChatModel.get_by_tid(6004)
        assert target_model is not None

        federation = await create_federation_via_command(test_client, owner_user, group, "Demote Test Fed", owner_model)

    # Promote then demote
    await FederationAdminService.promote_admin(federation, target_model.iid)
    assert await FederationAdminService.is_admin(federation, 6004) is True

    await FederationAdminService.demote_admin(federation, target_model.iid)
    assert await FederationAdminService.is_admin(federation, 6004) is False


@pytest.mark.asyncio
async def test_promote_duplicate_raises(test_client: TestClient) -> None:
    """Test that promoting an already-promoted user raises ValueError.

    Verifies:
    1. First promote succeeds
    2. Second promote of the same user raises ValueError
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=6005,
            first_name="DupPromOwner",
            username="dup_prom_owner",
            chat_id=-1001000006005,
            group_title="Dup Promote Group",
        )

        target_wrapper = test_client.create_user(user_id=6006, first_name="DupAdmin", username="dup_admin")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)
        target_model = await ChatModel.get_by_tid(6006)
        assert target_model is not None

        federation = await create_federation_via_command(test_client, owner_user, group, "Dup Promote Fed", owner_model)

    await FederationAdminService.promote_admin(federation, target_model.iid)

    with pytest.raises(ValueError, match="already an admin"):
        await FederationAdminService.promote_admin(federation, target_model.iid)


@pytest.mark.asyncio
async def test_demote_non_admin_raises(test_client: TestClient) -> None:
    """Test that demoting a non-admin user raises ValueError.

    Verifies:
    1. Demoting a user who was never promoted raises ValueError
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=6007,
            first_name="BadDemOwner",
            username="bad_dem_owner",
            chat_id=-1001000006007,
            group_title="Bad Demote Group",
        )

        target_wrapper = test_client.create_user(user_id=6008, first_name="NotAdmin", username="not_admin")
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)
        target_model = await ChatModel.get_by_tid(6008)
        assert target_model is not None

        federation = await create_federation_via_command(test_client, owner_user, group, "Bad Demote Fed", owner_model)

    with pytest.raises(ValueError, match="not an admin"):
        await FederationAdminService.demote_admin(federation, target_model.iid)


@pytest.mark.asyncio
async def test_owner_is_always_admin(test_client: TestClient) -> None:
    """Test that the federation owner is always recognized as admin.

    Verifies:
    1. The creator is admin without explicit promotion
    2. Permission checks pass for the owner
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=6009,
            first_name="OwnerAdmin",
            username="owner_admin",
            chat_id=-1001000006009,
            group_title="Owner Admin Group",
        )

        federation = await create_federation_via_command(test_client, owner_user, group, "Owner Admin Fed", owner_model)

    is_admin = await FederationAdminService.is_admin(federation, 6009)
    assert is_admin is True, "Owner should always be recognized as admin"

    is_owner = await FederationPermissionService.is_federation_owner(federation, 6009)
    assert is_owner is True

    can_ban = await FederationPermissionService.can_ban_in_federation(federation, 6009)
    assert can_ban is True


@pytest.mark.asyncio
async def test_non_admin_has_no_permissions(test_client: TestClient) -> None:
    """Test that a regular user has no federation admin permissions.

    Verifies:
    1. A user who is not owner or admin has no management permissions
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=6010,
            first_name="PermOwner",
            username="perm_owner",
            chat_id=-1001000006010,
            group_title="Perm Test Group",
        )

        random_wrapper = test_client.create_user(user_id=6011, first_name="RandomUser", username="random_user")
        await test_client.send_message(text="init", from_user=random_wrapper.user, chat=group)

        federation = await create_federation_via_command(test_client, owner_user, group, "Perm Test Fed", owner_model)

    is_admin = await FederationAdminService.is_admin(federation, 6011)
    assert is_admin is False

    can_manage = await FederationPermissionService.can_manage_federation(federation, 6011)
    assert can_manage is False

    can_ban = await FederationPermissionService.can_ban_in_federation(federation, 6011)
    assert can_ban is False
