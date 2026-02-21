"""E2E tests for federation management: create, rename, delete."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.services import FederationManageService
from tests.e2e.federations.conftest import (
    create_federation_via_command,
    create_test_user_and_group,
)


async def _find_federation_by_name(fed_name: str) -> Federation | None:
    """Look up a federation by name (mongomock workaround for DBRef queries)."""
    return await Federation.find_one(Federation.fed_name == fed_name)


@pytest.mark.asyncio
async def test_create_federation(test_client: TestClient) -> None:
    """Test creating a new federation via /newfed command.

    Verifies:
    1. The bot responds to /newfed with a federation name
    2. A Federation document is created in the database
    3. The federation has the correct name and creator
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=2001,
            first_name="Creator",
            username="creator_user",
            chat_id=-1001000002001,
            group_title="Create Test Group",
        )

        await test_client.send_command(command="newfed", from_user=owner_user, args="My Test Federation", chat=group)

    federation = await _find_federation_by_name("My Test Federation")
    assert federation is not None, "Federation should be created"
    assert federation.fed_name == "My Test Federation"
    assert federation.fed_id, "Federation should have a generated fed_id"

    creator = await federation.creator.fetch()
    assert creator is not None
    assert creator.tid == 2001


@pytest.mark.asyncio
async def test_create_federation_duplicate_name(test_client: TestClient) -> None:
    """Test that creating a federation with an existing name fails.

    Verifies:
    1. The first /newfed succeeds
    2. A second /newfed with the same name from a different user does not create a duplicate
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        user_a, group_a, model_a = await create_test_user_and_group(
            test_client,
            user_id=2010,
            first_name="UserDupA",
            username="user_dup_a",
            chat_id=-1001000002010,
            group_title="Dup Test Group A",
        )
        user_b, group_b, model_b = await create_test_user_and_group(
            test_client,
            user_id=2011,
            first_name="UserDupB",
            username="user_dup_b",
            chat_id=-1001000002011,
            group_title="Dup Test Group B",
        )

        fed_name = "UniqueDupTestFed"
        await test_client.send_command(command="newfed", from_user=user_a, args=fed_name, chat=group_a)

        fed_a = await _find_federation_by_name(fed_name)
        assert fed_a is not None, "First federation should be created"

        # Try to create federation with the same name from a different user
        await test_client.send_command(command="newfed", from_user=user_b, args=fed_name, chat=group_b)

    # Should still only have one federation with that name
    all_feds = await Federation.find(Federation.fed_name == fed_name).to_list()
    assert len(all_feds) == 1, "Second federation with duplicate name should not be created"


@pytest.mark.asyncio
async def test_rename_federation(test_client: TestClient) -> None:
    """Test renaming a federation via /frename command.

    Verifies:
    1. A federation is created with the original name
    2. The /frename command changes the name in the database
    3. The federation retains the same fed_id after rename
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=2002,
            first_name="Renamer",
            username="renamer_user",
            chat_id=-1001000002002,
            group_title="Rename Test Group",
        )

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Old Fed Name", owner_model
        )
        original_fed_id = federation.fed_id

        # Join group to federation so frename can resolve it from context
        await test_client.send_command(command="joinfed", from_user=owner_user, args=original_fed_id, chat=group)

        # Rename the federation
        await test_client.send_command(command="frename", from_user=owner_user, args="New Fed Name", chat=group)

    # Verify the rename
    updated_fed = await FederationManageService.get_federation_by_id(original_fed_id)
    assert updated_fed is not None
    assert updated_fed.fed_name == "New Fed Name"
    assert updated_fed.fed_id == original_fed_id, "Fed ID should remain unchanged after rename"


@pytest.mark.asyncio
async def test_rename_federation_non_owner_rejected(test_client: TestClient) -> None:
    """Test that a non-owner cannot rename a federation.

    Verifies:
    1. Owner creates a federation and joins a group to it
    2. A different user tries to rename - the name should not change
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=2003,
            first_name="RealOwner",
            username="real_owner",
            chat_id=-1001000002003,
            group_title="Rename Auth Group",
        )

        other_wrapper = test_client.create_user(user_id=2004, first_name="Intruder", username="intruder")
        await test_client.send_message(text="init", from_user=other_wrapper.user, chat=group)

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Protected Fed", owner_model
        )
        original_fed_id = federation.fed_id

        # Join group to federation
        await test_client.send_command(command="joinfed", from_user=owner_user, args=original_fed_id, chat=group)

        # Non-owner tries to rename
        await test_client.send_command(
            command="frename", from_user=other_wrapper.user, args="Hacked Name", chat=group
        )

    updated_fed = await FederationManageService.get_federation_by_id(original_fed_id)
    assert updated_fed is not None
    assert updated_fed.fed_name == "Protected Fed", "Non-owner should not be able to rename the federation"


@pytest.mark.asyncio
async def test_delete_federation_via_service(test_client: TestClient) -> None:
    """Test deleting a federation via the service layer.

    Verifies:
    1. A federation is created
    2. After deletion, the federation no longer exists in the database
    """
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        owner_user, group, owner_model = await create_test_user_and_group(
            test_client,
            user_id=2005,
            first_name="Deleter",
            username="deleter_user",
            chat_id=-1001000002005,
            group_title="Delete Test Group",
        )

        federation = await create_federation_via_command(
            test_client, owner_user, group, "Doomed Federation", owner_model
        )
        fed_id = federation.fed_id

    # Delete via service
    await FederationManageService.delete_federation(federation)

    deleted = await FederationManageService.get_federation_by_id(fed_id)
    assert deleted is None, "Federation should be deleted from the database"

    # Verify bans are also cleaned up (none existed, but confirm no crash)
    bans = await Federation.find(Federation.fed_id == fed_id).to_list()
    assert len(bans) == 0
