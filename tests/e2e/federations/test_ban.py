"""E2E tests for federation ban/unban flows."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId
from aiogram_test_framework import TestClient
from bson import DBRef

from sophie_bot.db.models.chat import ChatModel, UserInGroupModel
from sophie_bot.db.models.federations import FederationBan
from sophie_bot.modules.federations.exceptions import FederationBanValidationError
from sophie_bot.modules.federations.services import FederationBanService, FederationChatService, FederationManageService
from tests.e2e.federations.conftest import (
    create_federation_via_command,
    create_test_user_and_group,
)


def _extract_link_id(link_value: Any) -> Optional[PydanticObjectId]:
    """Extract the PydanticObjectId from a Beanie Link / DBRef / ChatModel / raw ObjectId."""
    if isinstance(link_value, DBRef):
        return PydanticObjectId(link_value.id)
    if isinstance(link_value, PydanticObjectId):
        return link_value
    # Beanie Document (e.g. ChatModel) — use its primary key
    if hasattr(link_value, "iid"):
        return PydanticObjectId(link_value.iid)
    if hasattr(link_value, "ref"):
        ref = link_value.ref
        if isinstance(ref, DBRef):
            return PydanticObjectId(ref.id)
    if hasattr(link_value, "to_ref"):
        ref = link_value.to_ref()
        return _extract_link_id(ref)
    if hasattr(link_value, "id"):
        return PydanticObjectId(link_value.id)
    return None


class _FakeFirstOrNone:
    """Wraps a list of UserInGroupModel and exposes ``first_or_none()``."""

    def __init__(self, results: list[UserInGroupModel]) -> None:
        self._results = results

    async def first_or_none(self) -> Optional[UserInGroupModel]:
        return self._results[0] if self._results else None

    async def to_list(self) -> list[UserInGroupModel]:
        return self._results


def _make_uig_find_patch(inserted_entries: list[UserInGroupModel]):
    """Return a replacement for ``UserInGroupModel.find`` that filters in Python.

    mongomock cannot evaluate DBRef sub-field queries (``user.$id``, ``group.$id``).
    This helper scans the pre-inserted *inserted_entries* list instead.
    """

    def _patched_find(*args: Any, **_kwargs: Any) -> _FakeFirstOrNone:
        # Parse the Beanie expression arguments to extract user_iid and group_iids.
        user_iid: Optional[PydanticObjectId] = None
        group_iids: Optional[set[PydanticObjectId]] = None

        for arg in args:
            # Beanie comparison expressions have `field` and `value` or similar attrs.
            # We inspect the dict representation instead for reliability.
            if hasattr(arg, "query"):
                query_dict = arg.query
            elif isinstance(arg, dict):
                query_dict = arg
            else:
                continue

            for key, val in query_dict.items():
                if "user" in key:
                    user_iid = PydanticObjectId(val) if not isinstance(val, PydanticObjectId) else val
                elif "group" in key:
                    if isinstance(val, dict) and "$in" in val:
                        group_iids = {PydanticObjectId(gid) for gid in val["$in"]}
                    else:
                        group_iids = {PydanticObjectId(val) if not isinstance(val, PydanticObjectId) else val}

        matched: list[UserInGroupModel] = []
        for entry in inserted_entries:
            entry_user_iid = _extract_link_id(entry.user)
            entry_group_iid = _extract_link_id(entry.group)

            if user_iid is not None and entry_user_iid != user_iid:
                continue
            if group_iids is not None and entry_group_iid not in group_iids:
                continue
            matched.append(entry)
        return _FakeFirstOrNone(matched)

    return _patched_find


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
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        # Create three federations with owners and groups
        user_a, group_a, model_a = await create_test_user_and_group(
            test_client,
            user_id=4030,
            first_name="LazyOwnerA",
            username="lazy_owner_a",
            chat_id=-1001000004030,
            group_title="Lazy Group A",
        )
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

    # Build a patched UserInGroupModel.find that filters in Python instead of
    # relying on DBRef sub-field queries (which mongomock cannot handle).
    uig_entries = [user_in_group_a, user_in_group_b, user_in_group_c]

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
    # Patch UserInGroupModel.find to work around mongomock DBRef query limitation.
    # Import the specific module where UserInGroupModel is used to ensure proper patching.
    from sophie_bot.modules.federations.services import ban as ban_service_module

    with patch.object(ban_service_module.UserInGroupModel, "find", _make_uig_find_patch(uig_entries)):
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
    admin_mock = AsyncMock(return_value=True)

    with patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", admin_mock):
        # Create two federations
        user_a, group_a, model_a = await create_test_user_and_group(
            test_client,
            user_id=4040,
            first_name="SelectiveOwnerA",
            username="selective_owner_a",
            chat_id=-1001000004040,
            group_title="Selective Group A",
        )
        user_b, group_b, model_b = await create_test_user_and_group(
            test_client,
            user_id=4041,
            first_name="SelectiveOwnerB",
            username="selective_owner_b",
            chat_id=-1001000004041,
            group_title="Selective Group B",
        )

        # Create target user who is ONLY in group B
        target_wrapper = test_client.create_user(
            user_id=4042, first_name="SelectiveTarget", username="selective_target"
        )
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
    # Import the specific module where UserInGroupModel is used to ensure proper patching.
    from sophie_bot.modules.federations.services import ban as ban_service_module

    with patch.object(ban_service_module.UserInGroupModel, "find", _make_uig_find_patch([user_in_group_b])):
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
