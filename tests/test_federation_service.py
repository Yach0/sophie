from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from bson import DBRef

from sophie_bot.modules.federations.exceptions import FederationContextError
from sophie_bot.modules.federations.services import (
    FederationAdminService,
    FederationBanService,
    FederationChatService,
    FederationManageService,
)
from sophie_bot.modules.federations.services import ban as ban_service_module


class FakeLink:
    def __init__(self, ref_value: PydanticObjectId) -> None:
        self._ref_value = ref_value

    def to_ref(self) -> PydanticObjectId:
        return self._ref_value


class FakeDbRefLink:
    def __init__(self, ref_value: PydanticObjectId) -> None:
        self._ref_value = ref_value

    def to_ref(self) -> DBRef:
        return DBRef("chats", self._ref_value)


@pytest.mark.asyncio
async def test_add_chat_to_federation_skips_existing_dbref_link() -> None:
    chat_iid = PydanticObjectId("507f1f77bcf86cd799439081")
    chat_model = MagicMock()
    chat_model.iid = chat_iid

    federation = MagicMock()
    federation.fed_id = "fed-main"
    federation.chats = [FakeDbRefLink(chat_iid)]
    federation.save = AsyncMock()

    with (
        patch(
            "sophie_bot.modules.federations.services.chat.ChatModel.get_by_iid",
            new=AsyncMock(return_value=chat_model),
        ),
        patch(
            "sophie_bot.modules.federations.services.chat.FederationCacheService.set_fed_id_for_chat",
            new=AsyncMock(),
        ) as cache_set_fed_id_mock,
        patch(
            "sophie_bot.modules.federations.services.chat.FederationCacheService.incr_chat_count",
            new=AsyncMock(),
        ) as cache_incr_count_mock,
    ):
        added = await FederationChatService.add_chat_to_federation(federation, chat_iid)

    assert added is False
    federation.save.assert_not_called()
    cache_set_fed_id_mock.assert_not_awaited()
    cache_incr_count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_chat_from_federation_removes_matching_dbref_link() -> None:
    removed_chat_iid = PydanticObjectId("507f1f77bcf86cd799439091")
    remaining_chat_iid = PydanticObjectId("507f1f77bcf86cd799439092")
    chat_model = MagicMock()
    chat_model.iid = removed_chat_iid

    federation = MagicMock()
    federation.fed_id = "fed-main"
    federation.chats = [FakeDbRefLink(removed_chat_iid), FakeDbRefLink(remaining_chat_iid)]
    federation.save = AsyncMock()

    with (
        patch(
            "sophie_bot.modules.federations.services.chat.ChatModel.get_by_iid",
            new=AsyncMock(return_value=chat_model),
        ),
        patch(
            "sophie_bot.modules.federations.services.chat.FederationCacheService.invalidate_federation_for_chat",
            new=AsyncMock(),
        ) as cache_invalidate_mock,
        patch(
            "sophie_bot.modules.federations.services.chat.FederationCacheService.incr_chat_count",
            new=AsyncMock(),
        ) as cache_incr_count_mock,
    ):
        removed = await FederationChatService.remove_chat_from_federation(federation, removed_chat_iid)

    assert removed is True
    assert len(federation.chats) == 1
    assert federation.chats[0].to_ref().id == remaining_chat_iid
    federation.save.assert_awaited_once()
    cache_invalidate_mock.assert_awaited_once_with(removed_chat_iid)
    cache_incr_count_mock.assert_awaited_once_with("fed-main", -1)


@pytest.mark.asyncio
async def test_ban_user_in_federation_chats_bans_only_detected_chats() -> None:
    user_tid = 1001
    user_iid = PydanticObjectId("507f1f77bcf86cd799439011")
    chat_one_iid = PydanticObjectId("507f1f77bcf86cd799439021")
    chat_two_iid = PydanticObjectId("507f1f77bcf86cd799439022")

    federation = MagicMock()
    federation.chats = [FakeLink(chat_one_iid), FakeLink(chat_two_iid)]

    ban = MagicMock()
    ban.banned_chats = []
    ban.save = AsyncMock()

    chat_one = MagicMock()
    chat_one.iid = chat_one_iid
    chat_one.tid = -10012345

    chat_two = MagicMock()
    chat_two.iid = chat_two_iid
    chat_two.tid = -10054321

    user_model = MagicMock()
    user_model.iid = user_iid

    user_in_group_entry = MagicMock()
    user_in_group_entry.group = FakeLink(chat_one_iid)

    chat_query = MagicMock()
    chat_query.to_list = AsyncMock(return_value=[chat_one, chat_two])

    user_in_group_query = MagicMock()
    user_in_group_query.to_list = AsyncMock(return_value=[user_in_group_entry])

    with (
        patch.object(ban_service_module.ChatModel, "iid", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "user", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "group", new=MagicMock(), create=True),
        patch("sophie_bot.modules.federations.services.ban.ChatModel.find", return_value=chat_query),
        patch(
            "sophie_bot.modules.federations.services.ban.ChatModel.get_by_tid",
            new=AsyncMock(return_value=user_model),
        ),
        patch("sophie_bot.modules.federations.services.ban.UserInGroupModel.find", return_value=user_in_group_query),
        patch(
            "sophie_bot.modules.federations.services.ban.restrict_ban_user",
            new=AsyncMock(return_value=True),
        ) as mock_restrict_ban_user,
    ):
        banned_count = await FederationBanService.ban_user_in_federation_chats(federation, ban, user_tid)

    assert banned_count == 1
    assert ban.banned_chats == [chat_one]
    ban.save.assert_awaited_once()
    mock_restrict_ban_user.assert_awaited_once_with(chat_one.tid, user_tid)


@pytest.mark.asyncio
async def test_ban_user_in_federation_chats_returns_zero_if_user_not_found() -> None:
    user_tid = 1001
    chat_iid = PydanticObjectId("507f1f77bcf86cd799439031")

    federation = MagicMock()
    federation.chats = [FakeLink(chat_iid)]

    ban = MagicMock()
    ban.banned_chats = []
    ban.save = AsyncMock()

    chat_model = MagicMock()
    chat_model.iid = chat_iid
    chat_model.tid = -10012345

    chat_query = MagicMock()
    chat_query.to_list = AsyncMock(return_value=[chat_model])

    with (
        patch.object(ban_service_module.ChatModel, "iid", new=MagicMock(), create=True),
        patch("sophie_bot.modules.federations.services.ban.ChatModel.find", return_value=chat_query),
        patch(
            "sophie_bot.modules.federations.services.ban.ChatModel.get_by_tid",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "sophie_bot.modules.federations.services.ban.UserInGroupModel.find",
            return_value=MagicMock(),
        ) as mock_user_in_group_find,
        patch(
            "sophie_bot.modules.federations.services.ban.restrict_ban_user",
            new=AsyncMock(return_value=True),
        ) as mock_restrict_ban_user,
    ):
        banned_count = await FederationBanService.ban_user_in_federation_chats(federation, ban, user_tid)

    assert banned_count == 0
    mock_user_in_group_find.assert_not_called()
    mock_restrict_ban_user.assert_not_called()
    ban.save.assert_not_called()


@pytest.mark.asyncio
async def test_ban_user_in_federation_chats_normalizes_dbref_group_links() -> None:
    user_tid = 1002
    user_iid = PydanticObjectId("507f1f77bcf86cd799439032")
    chat_iid = PydanticObjectId("507f1f77bcf86cd799439033")

    federation = MagicMock()
    federation.chats = [FakeDbRefLink(chat_iid)]

    ban = MagicMock()
    ban.banned_chats = []
    ban.save = AsyncMock()

    chat_model = MagicMock()
    chat_model.iid = chat_iid
    chat_model.tid = -10012346

    user_model = MagicMock()
    user_model.iid = user_iid

    user_in_group_entry = MagicMock()
    user_in_group_entry.group = FakeDbRefLink(chat_iid)

    chat_query = MagicMock()
    chat_query.to_list = AsyncMock(return_value=[chat_model])

    user_in_group_query = MagicMock()
    user_in_group_query.to_list = AsyncMock(return_value=[user_in_group_entry])

    with (
        patch.object(ban_service_module.ChatModel, "iid", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "user", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "group", new=MagicMock(), create=True),
        patch("sophie_bot.modules.federations.services.ban.ChatModel.find", return_value=chat_query),
        patch(
            "sophie_bot.modules.federations.services.ban.ChatModel.get_by_tid",
            new=AsyncMock(return_value=user_model),
        ),
        patch("sophie_bot.modules.federations.services.ban.UserInGroupModel.find", return_value=user_in_group_query),
        patch(
            "sophie_bot.modules.federations.services.ban.restrict_ban_user",
            new=AsyncMock(return_value=True),
        ) as mock_restrict_ban_user,
    ):
        banned_count = await FederationBanService.ban_user_in_federation_chats(federation, ban, user_tid)

    assert banned_count == 1
    assert ban.banned_chats == [chat_model]
    ban.save.assert_awaited_once()
    mock_restrict_ban_user.assert_awaited_once_with(chat_model.tid, user_tid)


@pytest.mark.asyncio
async def test_ban_user_in_federation_chats_includes_current_chat_without_seen_record() -> None:
    user_tid = 1003
    user_iid = PydanticObjectId("507f1f77bcf86cd799439034")
    chat_iid = PydanticObjectId("507f1f77bcf86cd799439035")

    federation = MagicMock()
    federation.chats = []

    ban = MagicMock()
    ban.banned_chats = []
    ban.save = AsyncMock()

    chat_model = MagicMock()
    chat_model.iid = chat_iid
    chat_model.tid = -10012347

    user_model = MagicMock()
    user_model.iid = user_iid

    chat_query = MagicMock()
    chat_query.to_list = AsyncMock(return_value=[chat_model])

    user_in_group_query = MagicMock()
    user_in_group_query.to_list = AsyncMock(return_value=[])

    with (
        patch.object(ban_service_module.ChatModel, "iid", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "user", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "group", new=MagicMock(), create=True),
        patch("sophie_bot.modules.federations.services.ban.ChatModel.find", return_value=chat_query),
        patch(
            "sophie_bot.modules.federations.services.ban.ChatModel.get_by_tid",
            new=AsyncMock(return_value=user_model),
        ),
        patch("sophie_bot.modules.federations.services.ban.UserInGroupModel.find", return_value=user_in_group_query),
        patch(
            "sophie_bot.modules.federations.services.ban.restrict_ban_user",
            new=AsyncMock(return_value=True),
        ) as mock_restrict_ban_user,
    ):
        banned_count = await FederationBanService.ban_user_in_federation_chats(
            federation,
            ban,
            user_tid,
            current_chat_iid=chat_iid,
        )

    assert banned_count == 1
    assert ban.banned_chats == [chat_model]
    ban.save.assert_awaited_once()
    mock_restrict_ban_user.assert_awaited_once_with(chat_model.tid, user_tid)


@pytest.mark.asyncio
async def test_promote_admin_raises_for_existing_admin_link() -> None:
    user_iid = PydanticObjectId("507f1f77bcf86cd799439041")

    federation = MagicMock()
    federation.admins = [FakeDbRefLink(user_iid)]
    federation.save = AsyncMock()

    with pytest.raises(ValueError, match="already an admin"):
        await FederationAdminService.promote_admin(federation, user_iid)

    federation.save.assert_not_called()


@pytest.mark.asyncio
async def test_demote_admin_removes_matching_admin_link() -> None:
    removed_admin_iid = PydanticObjectId("507f1f77bcf86cd799439051")
    remaining_admin_iid = PydanticObjectId("507f1f77bcf86cd799439052")

    federation = MagicMock()
    federation.admins = [FakeDbRefLink(removed_admin_iid), FakeDbRefLink(remaining_admin_iid)]
    federation.save = AsyncMock()

    await FederationAdminService.demote_admin(federation, removed_admin_iid)

    assert len(federation.admins) == 1
    assert federation.admins[0].to_ref().id == remaining_admin_iid
    federation.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_demote_admin_raises_for_missing_admin_link() -> None:
    existing_admin_iid = PydanticObjectId("507f1f77bcf86cd799439061")
    missing_admin_iid = PydanticObjectId("507f1f77bcf86cd799439062")

    federation = MagicMock()
    federation.admins = [FakeDbRefLink(existing_admin_iid)]
    federation.save = AsyncMock()

    with pytest.raises(ValueError, match="not an admin"):
        await FederationAdminService.demote_admin(federation, missing_admin_iid)

    federation.save.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_to_federation_rejects_self_subscription() -> None:
    federation = MagicMock()
    federation.fed_id = "fed-main"
    federation.subscribed = None
    federation.save = AsyncMock()

    with patch.object(
        FederationManageService,
        "get_federation_by_id",
        new=AsyncMock(return_value=federation),
    ):
        subscribed = await FederationManageService.subscribe_to_federation(federation, "fed-main")

    assert subscribed is False
    federation.save.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_to_federation_initializes_subscribed_list() -> None:
    federation = MagicMock()
    federation.fed_id = "fed-main"
    federation.subscribed = None
    federation.save = AsyncMock()

    target_federation = MagicMock()
    target_federation.fed_id = "fed-target"

    with patch.object(
        FederationManageService,
        "get_federation_by_id",
        new=AsyncMock(return_value=target_federation),
    ):
        subscribed = await FederationManageService.subscribe_to_federation(federation, "fed-target")

    assert subscribed is True
    assert federation.subscribed == ["fed-target"]
    federation.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsubscribe_from_federation_returns_false_for_missing_target() -> None:
    federation = MagicMock()
    federation.subscribed = ["fed-target"]
    federation.save = AsyncMock()

    unsubscribed = await FederationManageService.unsubscribe_from_federation(federation, "fed-other")

    assert unsubscribed is False
    assert federation.subscribed == ["fed-target"]
    federation.save.assert_not_called()


@pytest.mark.asyncio
async def test_get_subscription_chain_handles_cycle_without_duplicates() -> None:
    federation_main = MagicMock()
    federation_main.subscribed = ["fed-branch", "fed-other"]

    federation_branch = MagicMock()
    federation_branch.subscribed = ["fed-main"]

    federation_other = MagicMock()
    federation_other.subscribed = ["fed-leaf"]

    federation_leaf = MagicMock()
    federation_leaf.subscribed = []

    federation_map = {
        "fed-main": federation_main,
        "fed-branch": federation_branch,
        "fed-other": federation_other,
        "fed-leaf": federation_leaf,
    }

    async def fake_get_federation_by_id(fed_id: str) -> Any | None:
        return federation_map.get(fed_id)

    with patch.object(
        FederationManageService,
        "get_federation_by_id",
        new=AsyncMock(side_effect=fake_get_federation_by_id),
    ):
        chain = await FederationManageService.get_subscription_chain("fed-main")

    assert sorted(chain) == ["fed-branch", "fed-leaf", "fed-other"]


@pytest.mark.asyncio
async def test_get_federation_with_user_multiple_federations_raises() -> None:
    user_id = 21001
    user_model = MagicMock()
    user_model.iid = PydanticObjectId("507f1f77bcf86cd799439071")

    with (
        patch(
            "sophie_bot.modules.federations.services.manage.ChatModel.get_by_tid",
            new=AsyncMock(return_value=user_model),
        ),
        patch.object(
            FederationManageService,
            "get_federations_by_creator",
            new=AsyncMock(return_value=[MagicMock(), MagicMock()]),
        ),
    ):
        with pytest.raises(FederationContextError, match="multiple federations"):
            await FederationManageService.get_federation(fed_id_arg=None, connection=None, user_id=user_id)
