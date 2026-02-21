from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.services import FederationManageService


@pytest.mark.asyncio
async def test_federation_subscribe_flow(test_client: TestClient) -> None:
    """Test full federation subscription flow.

    Note: ``check_user_admin_permissions`` is mocked because mongomock does not
    support ``$id`` sub-queries on DBRef fields (e.g. ``ChatAdminModel.chat.id``).
    All other parts of the flow (handler logic, DB writes, service calls) are real.
    """
    # Create mock users
    user_a = test_client.create_user(user_id=1001, first_name="User A", username="user_a")
    user_b = test_client.create_user(user_id=1002, first_name="User B", username="user_b")

    # Create mock groups
    group_a = ChatFactory.create_group(chat_id=-1001000000001, title="Group A")
    group_b = ChatFactory.create_group(chat_id=-1001000000002, title="Group B")

    # Save chats in DB via middleware so that ChatModel records exist
    await test_client.send_message(text="hello", from_user=user_a.user, chat=group_a)
    await test_client.send_message(text="hello", from_user=user_b.user, chat=group_b)

    user_a_model = await ChatModel.get_by_tid(1001)
    user_b_model = await ChatModel.get_by_tid(1002)
    assert user_a_model is not None
    assert user_b_model is not None

    # Mock check_user_admin_permissions to always grant creator rights.
    # mongomock cannot query DBRef sub-fields (chat.$id), so the real
    # ChatAdminModel lookup always returns None in tests.
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        # 1. User A creates Federation A
        await test_client.send_command(command="newfed", from_user=user_a.user, args="Federation A", chat=group_a)

        # Get Fed A ID from DB (use fed_name lookup; mongomock can't query DBRef sub-fields)
        fed_a = await Federation.find_one(Federation.fed_name == "Federation A")
        assert fed_a is not None, "Federation A should be created"

        # User A joins Group A to Fed A
        await test_client.send_command(command="joinfed", from_user=user_a.user, args=fed_a.fed_id, chat=group_a)

        # Verify the chat was added to the federation
        updated_fed_a = await FederationManageService.get_federation_by_id(fed_a.fed_id)
        assert updated_fed_a is not None
        assert len(updated_fed_a.chats) == 1, "Group A should be joined to Federation A"

        # 2. User B creates Federation B
        await test_client.send_command(command="newfed", from_user=user_b.user, args="Federation B", chat=group_b)

        # Get Fed B ID from DB (use fed_name lookup; mongomock can't query DBRef sub-fields)
        fed_b = await Federation.find_one(Federation.fed_name == "Federation B")
        assert fed_b is not None, "Federation B should be created"

        # User B joins Group B to Fed B
        await test_client.send_command(command="joinfed", from_user=user_b.user, args=fed_b.fed_id, chat=group_b)

        # 3. User A subscribes Fed A to Fed B
        await test_client.send_command(command="fsub", from_user=user_a.user, args=fed_b.fed_id, chat=group_a)

    # Verify DB state
    updated_fed_a = await FederationManageService.get_federation_by_id(fed_a.fed_id)
    assert updated_fed_a is not None
    assert updated_fed_a.subscribed is not None
    assert fed_b.fed_id in updated_fed_a.subscribed, "Fed A should be subscribed to Fed B"

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        # Test duplicate subscription
        await test_client.send_command(command="fsub", from_user=user_a.user, args=fed_b.fed_id, chat=group_a)

        # 4. Unsubscribe
        await test_client.send_command(command="funsub", from_user=user_a.user, args=fed_b.fed_id, chat=group_a)

    # Verify DB state
    updated_fed_a = await FederationManageService.get_federation_by_id(fed_a.fed_id)
    assert updated_fed_a is not None
    assert fed_b.fed_id not in (updated_fed_a.subscribed or []), "Fed A should be unsubscribed from Fed B"


@pytest.mark.asyncio
async def test_federation_subscribe_to_self_is_rejected(test_client: TestClient) -> None:
    """Test that federation cannot subscribe to itself via /fsub.

    Verifies:
    1. Federation is created and joined to its chat
    2. /fsub with the same federation id does not create a subscription
    """
    user_owner = test_client.create_user(user_id=1101, first_name="Owner", username="owner_self_sub")

    group_owner = ChatFactory.create_group(chat_id=-1001000001101, title="Self Sub Group")
    await test_client.send_message(text="hello", from_user=user_owner.user, chat=group_owner)

    admin_mock = AsyncMock(return_value=True)
    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        await test_client.send_command(
            command="newfed", from_user=user_owner.user, args="Self Subscribe Federation", chat=group_owner
        )

        federation = await Federation.find_one(Federation.fed_name == "Self Subscribe Federation")
        assert federation is not None, "Federation should be created"

        await test_client.send_command(
            command="joinfed", from_user=user_owner.user, args=federation.fed_id, chat=group_owner
        )
        await test_client.send_command(
            command="fsub", from_user=user_owner.user, args=federation.fed_id, chat=group_owner
        )

    updated_federation = await FederationManageService.get_federation_by_id(federation.fed_id)
    assert updated_federation is not None
    assert federation.fed_id not in (updated_federation.subscribed or [])


@pytest.mark.asyncio
async def test_federation_unsubscribe_without_subscription_keeps_state(test_client: TestClient) -> None:
    """Test /funsub when federation is not subscribed to target federation.

    Verifies:
    1. Two federations are created and joined to their chats
    2. /funsub without prior subscription keeps source federation subscriptions empty
    """
    user_source = test_client.create_user(user_id=1102, first_name="Source", username="source_funsub")
    user_target = test_client.create_user(user_id=1103, first_name="Target", username="target_funsub")

    group_source = ChatFactory.create_group(chat_id=-1001000001102, title="Funsub Source Group")
    group_target = ChatFactory.create_group(chat_id=-1001000001103, title="Funsub Target Group")

    await test_client.send_message(text="hello", from_user=user_source.user, chat=group_source)
    await test_client.send_message(text="hello", from_user=user_target.user, chat=group_target)

    admin_mock = AsyncMock(return_value=True)
    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        await test_client.send_command(
            command="newfed", from_user=user_source.user, args="Funsub Source Federation", chat=group_source
        )
        await test_client.send_command(
            command="newfed", from_user=user_target.user, args="Funsub Target Federation", chat=group_target
        )

        source_federation = await Federation.find_one(Federation.fed_name == "Funsub Source Federation")
        target_federation = await Federation.find_one(Federation.fed_name == "Funsub Target Federation")
        assert source_federation is not None
        assert target_federation is not None

        await test_client.send_command(
            command="joinfed", from_user=user_source.user, args=source_federation.fed_id, chat=group_source
        )
        await test_client.send_command(
            command="joinfed", from_user=user_target.user, args=target_federation.fed_id, chat=group_target
        )

        await test_client.send_command(
            command="funsub", from_user=user_source.user, args=target_federation.fed_id, chat=group_source
        )

    updated_source_federation = await FederationManageService.get_federation_by_id(source_federation.fed_id)
    assert updated_source_federation is not None
    assert updated_source_federation.subscribed in (None, [])
