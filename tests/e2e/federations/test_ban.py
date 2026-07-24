"""E2E tests for federation ban/unban flows."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Chat, Message, User
from aiogram_test_framework import MessageFactory, TestClient, UpdateFactory
from aiogram_test_framework.types import CapturedRequest, RequestType

from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.db.models.federations import Federation, FederationBan, FederationTask
from sophie_bot.db.models.federations_enums import FederationTaskType
from sophie_bot.modules.federations.exceptions import FederationBanValidationError
from sophie_bot.modules.federations.services import FederationBanService, FederationChatService, FederationManageService
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, set_feature
from tests.e2e.federations.conftest import (
    create_federation_via_command,
)


async def _send_anonymous_fban(
    test_client: TestClient,
    group: Chat,
    *,
    args: str,
    title: str | None,
) -> list[CapturedRequest]:
    """Feed an /fban issued by an anonymous admin (anon-bot sender + author signature)."""
    anon_user = User(
        id=TELEGRAM_ANONYMOUS_ADMIN_BOT_ID,
        is_bot=True,
        first_name="GroupAnonymousBot",
        username="GroupAnonymousBot",
    )
    message = MessageFactory.create(text=f"/fban {args}", from_user=anon_user, chat=group).model_copy(
        update={"sender_chat": group, "author_signature": title}
    )
    start = len(test_client.capture)
    await test_client.dispatcher.feed_update(
        bot=test_client.bot,
        update=UpdateFactory.create_message_update(message),
    )
    return test_client.capture.all_requests[start:]


@pytest.mark.asyncio
async def test_fban_user_via_service(test_client: TestClient) -> None:
    """Test banning a user in a federation via the service layer.

    Verifies:
    1. A federation is created and a user is banned
    2. The ban record exists in the database with correct fields
    """
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4001,
        first_name="BanOwner",
        username="ban_owner",
        chat_id=-1001000004001,
        group_title="Ban Test Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    target_wrapper = test_client.create_user(user_id=4002, first_name="Target", username="ban_target")
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "Ban Test Fed", owner_model)

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
async def test_sfban_reply_records_banner_as_command_sender(test_client: TestClient) -> None:
    """Test that reply-based /sfban stores the command sender as the banner."""
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4050,
        first_name="ReplyBanOwner",
        username="reply_ban_owner",
        chat_id=-1001000004050,
        group_title="Reply Ban Test Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    target_wrapper = test_client.create_user(user_id=4051, first_name="ReplyTarget", username="reply_target")
    await test_client.send_message(text="spam", from_user=target_wrapper.user, chat=group)
    target_model = await ChatModel.get_by_tid(4051)
    assert target_model is not None

    federation = await create_federation_via_command(test_client, owner_user, group, "Reply Ban Test Fed", owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)

    target_message = MessageFactory.create(text="spam", from_user=target_wrapper.user, chat=group)
    command_message = MessageFactory.create_command(
        command="sfban",
        from_user=owner_user,
        chat=group,
    ).model_copy(update={"reply_to_message": target_message})
    await test_client.dispatcher.feed_update(
        bot=test_client.bot,
        update=UpdateFactory.create_message_update(command_message),
    )

    ban = await FederationBan.find_one(FederationBan.fed_id == federation.fed_id, FederationBan.user_id == 4051)
    assert ban is not None
    assert ban.by.to_ref().id == owner_model.iid
    assert ban.by.to_ref().id != target_model.iid


@pytest.mark.asyncio
async def test_fban_and_unfban_via_service(test_client: TestClient) -> None:
    """Test banning and unbanning a user via the service layer.

    Verifies:
    1. User is banned
    2. User is unbanned
    3. Ban record is removed from the database
    """
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4003,
        first_name="UnbanOwner",
        username="unban_owner",
        chat_id=-1001000004003,
        group_title="Unban Test Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    target_wrapper = test_client.create_user(user_id=4004, first_name="UnbanTarget", username="unban_target")
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "Unban Test Fed", owner_model)

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
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4005,
        first_name="RebanOwner",
        username="reban_owner",
        chat_id=-1001000004005,
        group_title="Reban Test Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    target_wrapper = test_client.create_user(user_id=4006, first_name="RebanTarget", username="reban_target")
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "Reban Test Fed", owner_model)

    # Ban user with first reason
    await FederationBanService.ban_user(federation, 4006, owner_model.iid, reason="first reason")

    # Ban again with updated reason
    updated_ban = await FederationBanService.ban_user(federation, 4006, owner_model.iid, reason="updated reason")
    assert updated_ban.reason == "updated reason"

    # Should still only have one ban record
    bans = await FederationBan.find(FederationBan.fed_id == federation.fed_id, FederationBan.user_id == 4006).to_list()
    assert len(bans) == 1, "Should not create duplicate ban records"


@pytest.mark.asyncio
async def test_unfban_nonexistent_user(test_client: TestClient) -> None:
    """Test that unbanning a user who is not banned returns failure.

    Verifies:
    1. Unbanning a non-banned user returns (False, None)
    """
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4007,
        first_name="NoUnbanOwner",
        username="no_unban_owner",
        chat_id=-1001000004007,
        group_title="No Unban Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    federation = await create_federation_via_command(test_client, owner_user, group, "No Unban Fed", owner_model)

    success, origin_ban = await FederationBanService.unban_user(federation.fed_id, 99999)
    assert success is False, "Unbanning a non-banned user should return False"
    assert origin_ban is None


@pytest.mark.asyncio
async def test_fban_cannot_ban_federation_owner(test_client: TestClient) -> None:
    """Test that the federation owner cannot be banned.

    Verifies:
    1. Attempting to ban the federation owner raises a validation error
    """
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4008,
        first_name="SelfBanOwner",
        username="self_ban_owner",
        chat_id=-1001000004008,
        group_title="Self Ban Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    # Create a second user to attempt the ban
    admin_wrapper = test_client.create_user(user_id=4009, first_name="Admin", username="fed_admin")
    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group)
    admin_model = await ChatModel.get_by_tid(4009)
    assert admin_model is not None

    federation = await create_federation_via_command(test_client, owner_user, group, "Self Ban Fed", owner_model)

    # Try to ban the federation owner
    with pytest.raises(FederationBanValidationError, match="Cannot ban the federation owner"):
        await FederationBanService.ban_user(federation, 4008, admin_model.iid)


@pytest.mark.asyncio
async def test_fban_cannot_ban_self(test_client: TestClient) -> None:
    """Test that a user cannot ban themselves.

    Verifies:
    1. Attempting to ban yourself raises a validation error
    """
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4010,
        first_name="SelfBanner",
        username="self_banner",
        chat_id=-1001000004010,
        group_title="Self Ban Group 2",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    federation = await create_federation_via_command(test_client, owner_user, group, "Self Ban Fed 2", owner_model)

    with pytest.raises(FederationBanValidationError, match="You cannot ban yourself"):
        await FederationBanService.ban_user(federation, 4010, owner_model.iid)


@pytest.mark.asyncio
async def test_ban_count_tracking(test_client: TestClient) -> None:
    """Test that the federation ban count is tracked correctly.

    Verifies:
    1. Ban count increases after banning users
    2. Ban count decreases after unbanning
    """
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4011,
        first_name="CountOwner",
        username="count_owner",
        chat_id=-1001000004011,
        group_title="Count Test Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    for target_tid in (4012, 4013, 4014):
        target_wrapper = test_client.create_user(
            user_id=target_tid, first_name=f"Target{target_tid}", username=f"target_{target_tid}"
        )
        await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "Count Test Fed", owner_model)

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
    user_a, group_a, model_a = await create_test_user_and_group(
        test_client,
        user_id=4020,
        first_name="ChainOwnerA",
        username="chain_owner_a",
        chat_id=-1001000004020,
        group_title="Chain Group A",
    )
    await grant_admin(group_a.id, user_a.id, creator=True)
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
    group_model_a = await ChatModel.get_by_tid(group_a.id)
    group_model_b = await ChatModel.get_by_tid(group_b.id)
    assert group_model_a is not None
    assert group_model_b is not None
    await FederationChatService.add_chat_to_federation(fed_a, group_model_a.iid)
    await FederationChatService.add_chat_to_federation(fed_b, group_model_b.iid)
    fed_a = await FederationManageService.get_federation_by_id(fed_a.fed_id)
    fed_b = await FederationManageService.get_federation_by_id(fed_b.fed_id)
    assert fed_a is not None
    assert fed_b is not None

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


@pytest.mark.asyncio
async def test_lazy_ban_transitive_subscription_chain(test_client: TestClient) -> None:
    """Test that lazy-ban works transitively through subscription chains.

    Verifies:
    1. Fed A subscribes to Fed B, Fed B subscribes to Fed C (chain: A → B → C)
    2. Target user is in chats of all three federations
    3. User is banned in Fed C
    4. User is automatically banned in Fed B and Fed A via lazy-ban
    """
    # Create three federations with owners and groups
    user_a, group_a, model_a = await create_test_user_and_group(
        test_client,
        user_id=4030,
        first_name="LazyOwnerA",
        username="lazy_owner_a",
        chat_id=-1001000004030,
        group_title="Lazy Group A",
    )
    await grant_admin(group_a.id, user_a.id, creator=True)
    user_b, group_b, model_b = await create_test_user_and_group(
        test_client,
        user_id=4031,
        first_name="LazyOwnerB",
        username="lazy_owner_b",
        chat_id=-1001000004031,
        group_title="Lazy Group B",
    )
    user_c, group_c, model_c = await create_test_user_and_group(
        test_client,
        user_id=4032,
        first_name="LazyOwnerC",
        username="lazy_owner_c",
        chat_id=-1001000004032,
        group_title="Lazy Group C",
    )

    # Create target user who will be in all three groups
    target_wrapper = test_client.create_user(user_id=4033, first_name="LazyTarget", username="lazy_target")
    # User sends messages in all three groups
    await test_client.send_message(text="init A", from_user=target_wrapper.user, chat=group_a)
    await test_client.send_message(text="init B", from_user=target_wrapper.user, chat=group_b)
    await test_client.send_message(text="init C", from_user=target_wrapper.user, chat=group_c)

    # Create federations
    fed_a = await create_federation_via_command(test_client, user_a, group_a, "Lazy Fed A", model_a)
    fed_b = await create_federation_via_command(test_client, user_b, group_b, "Lazy Fed B", model_b)
    fed_c = await create_federation_via_command(test_client, user_c, group_c, "Lazy Fed C", model_c)

    # Join chats to their respective federations
    group_model_a = await ChatModel.get_by_tid(group_a.id)
    group_model_b = await ChatModel.get_by_tid(group_b.id)
    group_model_c = await ChatModel.get_by_tid(group_c.id)
    assert group_model_a is not None
    assert group_model_b is not None
    assert group_model_c is not None
    await FederationChatService.add_chat_to_federation(fed_a, group_model_a.iid)
    await FederationChatService.add_chat_to_federation(fed_b, group_model_b.iid)
    await FederationChatService.add_chat_to_federation(fed_c, group_model_c.iid)
    fed_a = await FederationManageService.get_federation_by_id(fed_a.fed_id)
    fed_b = await FederationManageService.get_federation_by_id(fed_b.fed_id)
    fed_c = await FederationManageService.get_federation_by_id(fed_c.fed_id)
    assert fed_a is not None
    assert fed_b is not None
    assert fed_c is not None

    # Get target user model and create UserInGroupModel entries manually
    # (mongomock doesn't handle Link relationships well in e2e tests)
    target_model = await ChatModel.get_by_tid(4033)
    assert target_model is not None, "Target user should exist in database"

    # Retrieve group chat models (created by SaveChatsMiddleware during init messages)
    group_model_a = await ChatModel.get_by_tid(group_a.id)
    group_model_b = await ChatModel.get_by_tid(group_b.id)
    group_model_c = await ChatModel.get_by_tid(group_c.id)
    assert group_model_a is not None, "Group A ChatModel should exist"
    assert group_model_b is not None, "Group B ChatModel should exist"
    assert group_model_c is not None, "Group C ChatModel should exist"

    # Create UserInGroupModel entries to simulate user being in all three groups
    user_in_group_a = UserInGroupModel(user=target_model, group=group_model_a, last_saw=target_model.last_saw)
    user_in_group_b = UserInGroupModel(user=target_model, group=group_model_b, last_saw=target_model.last_saw)
    user_in_group_c = UserInGroupModel(user=target_model, group=group_model_c, last_saw=target_model.last_saw)
    await user_in_group_a.insert()
    await user_in_group_b.insert()
    await user_in_group_c.insert()

    # Set up subscription chain: A → B → C
    # Fed A subscribes to Fed B
    success_a = await FederationManageService.subscribe_to_federation(fed_a, fed_b.fed_id)
    assert success_a is True, "Fed A should subscribe to Fed B"

    # Fed B subscribes to Fed C
    success_b = await FederationManageService.subscribe_to_federation(fed_b, fed_c.fed_id)
    assert success_b is True, "Fed B should subscribe to Fed C"

    # Verify the subscription chain is set up correctly
    chain_a = await FederationManageService.get_subscription_chain(fed_a.fed_id)
    assert fed_b.fed_id in chain_a, "Fed A should have Fed B in subscription chain"
    assert fed_c.fed_id in chain_a, "Fed A should have Fed C in subscription chain via B"

    # Verify reverse chain from Fed C
    reverse_chain_c = await FederationManageService.get_subscribed_by_chain(fed_c.fed_id)
    reverse_fed_ids = [f.fed_id for f in reverse_chain_c]
    assert fed_b.fed_id in reverse_fed_ids, "Fed C should have Fed B in reverse chain"
    assert fed_a.fed_id in reverse_fed_ids, "Fed C should have Fed A in reverse chain via B"

    # Ban user in Fed C (the "root" of the chain)
    ban_c = await FederationBanService.ban_user(fed_c, 4033, model_c.iid, reason="transitive lazy ban test")
    assert ban_c is not None
    assert ban_c.fed_id == fed_c.fed_id

    # Trigger lazy-ban in subscribing federations.
    lazy_bans = await FederationBanService.lazy_ban_in_subscribing_federations(
        fed_c, 4033, model_c.iid, reason="transitive lazy ban test"
    )

    # Should have banned in Fed B and Fed A (2 lazy bans)
    assert len(lazy_bans) == 2, f"Expected 2 lazy bans (B and A), got {len(lazy_bans)}"

    lazy_ban_fed_ids = [fed.fed_id for fed, _ in lazy_bans]
    assert fed_b.fed_id in lazy_ban_fed_ids, "User should be banned in Fed B via lazy-ban"
    assert fed_a.fed_id in lazy_ban_fed_ids, "User should be banned in Fed A via lazy-ban"

    # Verify bans have origin_fed set correctly
    for fed, ban in lazy_bans:
        assert ban.origin_fed == fed_c.fed_id, f"Ban in {fed.fed_id} should have origin_fed set to Fed C"

    # Verify user is actually banned in all three federations
    is_banned_a = await FederationBanService.is_user_banned(fed_a.fed_id, 4033)
    is_banned_b = await FederationBanService.is_user_banned(fed_b.fed_id, 4033)
    is_banned_c = await FederationBanService.is_user_banned(fed_c.fed_id, 4033)

    assert is_banned_a is not None, "User should be banned in Fed A"
    assert is_banned_b is not None, "User should be banned in Fed B"
    assert is_banned_c is not None, "User should be banned in Fed C"


@pytest.mark.asyncio
async def test_lazy_ban_only_bans_if_user_present(test_client: TestClient) -> None:
    """Test that lazy-ban only bans users in federations where they are present.

    Verifies:
    1. Fed A subscribes to Fed B
    2. Target user is ONLY in Fed B's chat (not Fed A's)
    3. User is banned in Fed B
    4. User is NOT banned in Fed A via lazy-ban (user not present there)
    """
    # Create two federations
    user_a, group_a, model_a = await create_test_user_and_group(
        test_client,
        user_id=4040,
        first_name="SelectiveOwnerA",
        username="selective_owner_a",
        chat_id=-1001000004040,
        group_title="Selective Group A",
    )
    await grant_admin(group_a.id, user_a.id, creator=True)
    user_b, group_b, model_b = await create_test_user_and_group(
        test_client,
        user_id=4041,
        first_name="SelectiveOwnerB",
        username="selective_owner_b",
        chat_id=-1001000004041,
        group_title="Selective Group B",
    )

    # Create target user who is ONLY in group B
    target_wrapper = test_client.create_user(user_id=4042, first_name="SelectiveTarget", username="selective_target")
    # User only sends message in group B, NOT in group A
    await test_client.send_message(text="init B", from_user=target_wrapper.user, chat=group_b)

    # Create federations
    fed_a = await create_federation_via_command(test_client, user_a, group_a, "Selective Fed A", model_a)
    fed_b = await create_federation_via_command(test_client, user_b, group_b, "Selective Fed B", model_b)

    # Join chats to their respective federations
    group_model_a = await ChatModel.get_by_tid(group_a.id)
    group_model_b = await ChatModel.get_by_tid(group_b.id)
    assert group_model_a is not None
    assert group_model_b is not None
    await FederationChatService.add_chat_to_federation(fed_a, group_model_a.iid)
    await FederationChatService.add_chat_to_federation(fed_b, group_model_b.iid)
    fed_a = await FederationManageService.get_federation_by_id(fed_a.fed_id)
    fed_b = await FederationManageService.get_federation_by_id(fed_b.fed_id)
    assert fed_a is not None
    assert fed_b is not None

    # Get target user model and create UserInGroupModel entry only for group B
    # (mongomock doesn't handle Link relationships well in e2e tests)
    target_model = await ChatModel.get_by_tid(4042)
    assert target_model is not None, "Target user should exist in database"

    # Retrieve group chat model for group B
    group_model_b = await ChatModel.get_by_tid(group_b.id)
    assert group_model_b is not None, "Group B ChatModel should exist"

    # Create UserInGroupModel entry ONLY for group B (user is NOT in group A)
    user_in_group_b = UserInGroupModel(user=target_model, group=group_model_b, last_saw=target_model.last_saw)
    await user_in_group_b.insert()

    # Set up subscription: A → B
    success = await FederationManageService.subscribe_to_federation(fed_a, fed_b.fed_id)
    assert success is True, "Fed A should subscribe to Fed B"

    # Ban user in Fed B
    ban_b = await FederationBanService.ban_user(fed_b, 4042, model_b.iid, reason="selective lazy ban test")
    assert ban_b is not None

    # Trigger lazy-ban
    lazy_bans = await FederationBanService.lazy_ban_in_subscribing_federations(
        fed_b, 4042, model_b.iid, reason="selective lazy ban test"
    )

    # Should have banned ONLY in Fed A where user is NOT present
    # So actually 0 lazy bans since user isn't in Fed A's chats
    assert len(lazy_bans) == 0, "User should NOT be banned in Fed A (not present in any of its chats)"

    # Verify user is banned in Fed B but NOT in Fed A
    is_banned_a = await FederationBanService.is_user_banned(fed_a.fed_id, 4042)
    is_banned_b = await FederationBanService.is_user_banned(fed_b.fed_id, 4042)

    assert is_banned_a is None, "User should NOT be banned in Fed A (not present)"
    assert is_banned_b is not None, "User should be banned in Fed B"


async def _create_subscribed_fed_pair(
    test_client: TestClient,
    *,
    owner_a_tid: int,
    owner_b_tid: int,
    group_a_tid: int,
    group_b_tid: int,
    name_prefix: str,
) -> tuple[Federation, Federation, ChatModel]:
    """Create Fed A and Fed B (each with one chat) and subscribe Fed A to Fed B."""
    user_a, group_a, model_a = await create_test_user_and_group(
        test_client,
        user_id=owner_a_tid,
        first_name=f"{name_prefix}OwnerA",
        username=f"{name_prefix.lower()}_owner_a",
        chat_id=group_a_tid,
        group_title=f"{name_prefix} Group A",
    )
    await grant_admin(group_a.id, user_a.id, creator=True)
    user_b, group_b, model_b = await create_test_user_and_group(
        test_client,
        user_id=owner_b_tid,
        first_name=f"{name_prefix}OwnerB",
        username=f"{name_prefix.lower()}_owner_b",
        chat_id=group_b_tid,
        group_title=f"{name_prefix} Group B",
    )

    fed_a = await create_federation_via_command(test_client, user_a, group_a, f"{name_prefix} Fed A", model_a)
    fed_b = await create_federation_via_command(test_client, user_b, group_b, f"{name_prefix} Fed B", model_b)

    subscribed = await FederationManageService.subscribe_to_federation(fed_a, fed_b.fed_id)
    assert subscribed is True, "Fed A should subscribe to Fed B"

    return fed_a, fed_b, model_b


async def _insert_inherited_ban(fed_id: str, origin_fed_id: str, user_tid: int, by_model: ChatModel) -> FederationBan:
    """Insert the ban row shape that lazy_ban_in_subscribing_federations produces."""
    ban = FederationBan(
        fed_id=fed_id,
        user_id=user_tid,
        time=datetime.now(timezone.utc),
        by=by_model,
        reason="inherited ban",
        origin_fed=origin_fed_id,
    )
    await ban.insert()
    return ban


@pytest.mark.asyncio
async def test_unfban_blocked_while_origin_subscription_is_live(test_client: TestClient) -> None:
    """An inherited ban stays in place while the subscription and the origin ban both exist."""
    fed_a, fed_b, model_b = await _create_subscribed_fed_pair(
        test_client,
        owner_a_tid=4060,
        owner_b_tid=4061,
        group_a_tid=-1001000004060,
        group_b_tid=-1001000004061,
        name_prefix="LiveSub",
    )

    await FederationBanService.ban_user(fed_b, 4062, model_b.iid, reason="origin ban")
    await _insert_inherited_ban(fed_a.fed_id, fed_b.fed_id, 4062, model_b)

    success, blocking_ban = await FederationBanService.unban_user(fed_a.fed_id, 4062)
    assert success is False, "Unban must be refused while the origin subscription still applies"
    assert blocking_ban is not None
    assert blocking_ban.origin_fed == fed_b.fed_id
    assert await FederationBanService.is_user_banned(fed_a.fed_id, 4062) is not None


@pytest.mark.asyncio
async def test_unfban_allowed_after_unsubscribing_from_origin_federation(test_client: TestClient) -> None:
    """The remedy the bot prints (/funsub the parent fed) makes the inherited ban removable."""
    fed_a, fed_b, model_b = await _create_subscribed_fed_pair(
        test_client,
        owner_a_tid=4063,
        owner_b_tid=4064,
        group_a_tid=-1001000004063,
        group_b_tid=-1001000004064,
        name_prefix="Unsub",
    )

    await FederationBanService.ban_user(fed_b, 4065, model_b.iid, reason="origin ban")
    await _insert_inherited_ban(fed_a.fed_id, fed_b.fed_id, 4065, model_b)

    unsubscribed = await FederationManageService.unsubscribe_from_federation(fed_a, fed_b.fed_id)
    assert unsubscribed is True

    success, blocking_ban = await FederationBanService.unban_user(fed_a.fed_id, 4065)
    assert success is True, "Unban must succeed once Fed A no longer subscribes to Fed B"
    assert blocking_ban is None
    assert await FederationBanService.is_user_banned(fed_a.fed_id, 4065) is None


@pytest.mark.asyncio
async def test_unfban_allowed_after_origin_ban_is_lifted(test_client: TestClient) -> None:
    """An inherited ban is removable once the origin federation's own ban is gone."""
    fed_a, fed_b, model_b = await _create_subscribed_fed_pair(
        test_client,
        owner_a_tid=4066,
        owner_b_tid=4067,
        group_a_tid=-1001000004066,
        group_b_tid=-1001000004067,
        name_prefix="OriginLifted",
    )

    await FederationBanService.ban_user(fed_b, 4068, model_b.iid, reason="origin ban")
    await _insert_inherited_ban(fed_a.fed_id, fed_b.fed_id, 4068, model_b)

    origin_unbanned, _blocking = await FederationBanService.unban_user(fed_b.fed_id, 4068)
    assert origin_unbanned is True

    success, blocking_ban = await FederationBanService.unban_user(fed_a.fed_id, 4068)
    assert success is True, "Unban must succeed once the origin federation's ban no longer exists"
    assert blocking_ban is None
    assert await FederationBanService.is_user_banned(fed_a.fed_id, 4068) is None


@pytest.mark.asyncio
async def test_fban_queues_propagation_task_when_reply_cannot_be_sent(test_client: TestClient) -> None:
    """Losing send rights must not cost the ban its propagation task."""
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=4070,
        first_name="NoReplyOwner",
        username="no_reply_owner",
        chat_id=-1001000004070,
        group_title="No Reply Group",
    )
    await grant_admin(group.id, owner_user.id, creator=True)

    target_wrapper = test_client.create_user(user_id=4071, first_name="NoReplyTarget", username="no_reply_target")
    await test_client.send_message(text="spam", from_user=target_wrapper.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "No Reply Fed", owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)

    target_message = MessageFactory.create(text="spam", from_user=target_wrapper.user, chat=group)
    command_message = MessageFactory.create_command(
        command="fban",
        from_user=owner_user,
        chat=group,
    ).model_copy(update={"reply_to_message": target_message})

    forbidden = TelegramForbiddenError(method=None, message="Forbidden: bot is not a member of the group chat")  # type: ignore[arg-type]
    with patch.object(Message, "reply", AsyncMock(side_effect=forbidden)):
        await test_client.dispatcher.feed_update(
            bot=test_client.bot,
            update=UpdateFactory.create_message_update(command_message),
        )

    ban = await FederationBan.find_one(FederationBan.fed_id == federation.fed_id, FederationBan.user_id == 4071)
    assert ban is not None, "The ban record should still be written"

    task = await FederationTask.find_one(
        FederationTask.fed_id == federation.fed_id,
        FederationTask.task_type == FederationTaskType.BAN,
        FederationTask.target_user_id == 4071,
    )
    assert task is not None, "The propagation task must be queued even when the reply could not be sent"
    assert task.reply_message_id is None


# ---------------------------------------------------------------------------
# Command-flow coverage: /fban, /unfban, /fcheck through the dispatcher
# ---------------------------------------------------------------------------


async def _fed_with_joined_member(
    test_client: TestClient, *, owner_tid: int, chat_tid: int, target_tid: int, fed_name: str
):
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client, user_id=owner_tid, chat_id=chat_tid, first_name="BanOwner", group_title=fed_name
    )
    await grant_admin(group.id, owner_user.id, creator=True)
    await grant_bot_admin(group.id)
    target = test_client.create_user(user_id=target_tid, first_name="Spammer", username=f"spammer_{target_tid}")
    await test_client.send_message(text="init", from_user=target.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, fed_name, owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)
    return owner_user, group, federation, target.user


@pytest.mark.asyncio
async def test_fban_command_records_ban_and_bans_in_chat(test_client: TestClient) -> None:
    owner_user, group, federation, target = await _fed_with_joined_member(
        test_client, owner_tid=6040, chat_tid=-1001000006040, target_tid=6041, fed_name="Fban Cmd Fed"
    )

    requests = await test_client.send_command(
        command="fban", from_user=owner_user, args=f"{target.id} spamming", chat=group
    )

    assert await FederationBanService.is_user_banned(federation.fed_id, target.id) is not None, (
        "The federation ban record should exist"
    )
    bans = [
        request
        for request in requests
        if request.request_type == RequestType.BAN_CHAT_MEMBER and request.params.get("user_id") == target.id
    ]
    assert bans, "The user should be banned in the current federation chat immediately"


@pytest.mark.asyncio
async def test_unfban_command_lifts_ban(test_client: TestClient) -> None:
    owner_user, group, federation, target = await _fed_with_joined_member(
        test_client, owner_tid=6042, chat_tid=-1001000006042, target_tid=6043, fed_name="Unfban Cmd Fed"
    )
    await test_client.send_command(command="fban", from_user=owner_user, args=str(target.id), chat=group)
    assert await FederationBanService.is_user_banned(federation.fed_id, target.id) is not None

    await test_client.send_command(command="unfban", from_user=owner_user, args=str(target.id), chat=group)

    assert await FederationBanService.is_user_banned(federation.fed_id, target.id) is None, (
        "The federation ban should be lifted after /unfban"
    )


@pytest.mark.asyncio
async def test_fcheck_group_reports_ban_status(test_client: TestClient) -> None:
    owner_user, group, federation, target = await _fed_with_joined_member(
        test_client, owner_tid=6044, chat_tid=-1001000006044, target_tid=6045, fed_name="Fcheck Cmd Fed"
    )

    before = await test_client.send_command(command="fcheck", from_user=owner_user, args=str(target.id), chat=group)
    assert any("not banned" in (request.text or "").lower() for request in before)

    await test_client.send_command(command="fban", from_user=owner_user, args=str(target.id), chat=group)

    after = await test_client.send_command(command="fcheck", from_user=owner_user, args=str(target.id), chat=group)
    assert any("Banned in current fed" in (request.text or "") for request in after)


# ---------------------------------------------------------------------------
# Anonymous admin /fban (feature-flagged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fban_anonymous_admin_resolves_real_banner_and_anonymizes_reply(test_client: TestClient) -> None:
    """Flag ON: an anonymous fed admin can /fban; the real admin is recorded and the reply is anonymised."""
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=6080,
        first_name="AnonBoss",
        username="anon_boss",
        chat_id=-1001000006080,
        group_title="Anon Fban Fed",
    )
    # The fed owner runs the command as an anonymous chat admin with the "Boss" title.
    await grant_admin(group.id, owner_user.id, creator=True, is_anonymous=True, custom_title="Boss")
    await grant_bot_admin(group.id)

    target = test_client.create_user(user_id=6081, first_name="AnonTarget", username="anon_target")
    await test_client.send_message(text="spam", from_user=target.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "Anon Fban Fed", owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)

    await set_feature("fban_anonymous_admin", True)

    requests = await _send_anonymous_fban(test_client, group, args=f"{target.user.id} spamming", title="Boss")

    ban = await FederationBan.find_one(
        FederationBan.fed_id == federation.fed_id, FederationBan.user_id == target.user.id
    )
    assert ban is not None, "the anonymous fed admin should be permitted to ban"
    assert ban.by.to_ref().id == owner_model.iid, "the ban must record the real admin behind the anonymous sender"

    assert any("Anonymous admin" in (request.text or "") for request in requests), (
        "the public reply must anonymise the banner"
    )
    assert not any("AnonBoss" in (request.text or "") for request in requests), (
        "the real admin name must not leak into the public reply"
    )

    task = await FederationTask.find_one(
        FederationTask.fed_id == federation.fed_id,
        FederationTask.task_type == FederationTaskType.BAN,
        FederationTask.target_user_id == target.user.id,
    )
    assert task is not None
    assert task.banner_anonymous is True, "the propagation task must carry the anonymisation flag"
    assert task.user.to_ref().id == owner_model.iid, "the task must record the real admin as the banner"


@pytest.mark.asyncio
async def test_fban_anonymous_admin_denied_when_flag_disabled(test_client: TestClient) -> None:
    """Flag OFF: an anonymous admin gets today's behaviour - resolved to the anon-bot id, no ban."""
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=6082,
        first_name="AnonBossOff",
        username="anon_boss_off",
        chat_id=-1001000006082,
        group_title="Anon Fban Off Fed",
    )
    await grant_admin(group.id, owner_user.id, creator=True, is_anonymous=True, custom_title="Boss")
    await grant_bot_admin(group.id)

    target = test_client.create_user(user_id=6083, first_name="AnonTargetOff", username="anon_target_off")
    await test_client.send_message(text="spam", from_user=target.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "Anon Fban Off Fed", owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)

    # Flag defaults to False; assert explicitly rather than relying on the default.
    await set_feature("fban_anonymous_admin", False)

    requests = await _send_anonymous_fban(test_client, group, args=f"{target.user.id} spamming", title="Boss")

    ban = await FederationBan.find_one(
        FederationBan.fed_id == federation.fed_id, FederationBan.user_id == target.user.id
    )
    assert ban is None, "with the flag off the anonymous admin must not be able to ban"
    assert not any("Anonymous admin" in (request.text or "") for request in requests), (
        "no anonymisation path should run when the flag is off"
    )
    assert any(
        "permission" in (request.text or "").lower() or "could not resolve" in (request.text or "").lower()
        for request in requests
    ), "the anonymous admin must hit a denial/unresolved path when the flag is off"


@pytest.mark.asyncio
async def test_fban_anonymous_admin_ambiguous_title_reports_error(test_client: TestClient) -> None:
    """Flag ON: two anonymous admins share the signature title, so the identity is ambiguous - refuse."""
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=6084,
        first_name="AmbiguousOwner",
        username="ambiguous_owner",
        chat_id=-1001000006084,
        group_title="Ambiguous Fban Fed",
    )
    await grant_admin(group.id, owner_user.id, creator=True)
    await grant_bot_admin(group.id)

    twin_one = test_client.create_user(user_id=6085, first_name="TwinOne", username="twin_one")
    twin_two = test_client.create_user(user_id=6086, first_name="TwinTwo", username="twin_two")
    await test_client.send_message(text="init", from_user=twin_one.user, chat=group)
    await test_client.send_message(text="init", from_user=twin_two.user, chat=group)
    await grant_admin(group.id, twin_one.user.id, is_anonymous=True, custom_title="Twin")
    await grant_admin(group.id, twin_two.user.id, is_anonymous=True, custom_title="Twin")

    target = test_client.create_user(user_id=6087, first_name="AmbiguousTarget", username="ambiguous_target")
    await test_client.send_message(text="spam", from_user=target.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "Ambiguous Fban Fed", owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)

    await set_feature("fban_anonymous_admin", True)

    requests = await _send_anonymous_fban(test_client, group, args=f"{target.user.id} spamming", title="Twin")

    ban = await FederationBan.find_one(
        FederationBan.fed_id == federation.fed_id, FederationBan.user_id == target.user.id
    )
    assert ban is None, "an ambiguous anonymous identity must not result in a ban"
    assert any("Multiple anonymous admins share this title" in (request.text or "") for request in requests)


@pytest.mark.asyncio
async def test_fban_anonymous_admin_missing_title_reports_error(test_client: TestClient) -> None:
    """Flag ON: an anonymous admin with no custom title cannot be resolved - refuse with a clear message."""
    owner_user, group, owner_model = await create_test_user_and_group(
        test_client,
        user_id=6088,
        first_name="NoTitleOwner",
        username="no_title_owner",
        chat_id=-1001000006088,
        group_title="No Title Fban Fed",
    )
    await grant_admin(group.id, owner_user.id, creator=True, is_anonymous=True)
    await grant_bot_admin(group.id)

    target = test_client.create_user(user_id=6089, first_name="NoTitleTarget", username="no_title_target")
    await test_client.send_message(text="spam", from_user=target.user, chat=group)

    federation = await create_federation_via_command(test_client, owner_user, group, "No Title Fban Fed", owner_model)
    await test_client.send_command(command="joinfed", from_user=owner_user, args=federation.fed_id, chat=group)

    await set_feature("fban_anonymous_admin", True)

    requests = await _send_anonymous_fban(test_client, group, args=f"{target.user.id} spamming", title=None)

    ban = await FederationBan.find_one(
        FederationBan.fed_id == federation.fed_id, FederationBan.user_id == target.user.id
    )
    assert ban is None, "an anonymous admin with no title must not result in a ban"
    assert any("custom admin title" in (request.text or "") for request in requests)
