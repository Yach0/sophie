from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from bson import DBRef

from sophie_bot.modules.federations.services import federation as federation_service_module
from sophie_bot.modules.federations.services.federation import FederationService


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
        patch.object(federation_service_module.ChatModel, "iid", new=MagicMock(), create=True),
        patch.object(federation_service_module.UserInGroupModel, "user", new=MagicMock(), create=True),
        patch.object(federation_service_module.UserInGroupModel, "group", new=MagicMock(), create=True),
        patch("sophie_bot.modules.federations.services.federation.ChatModel.find", return_value=chat_query),
        patch(
            "sophie_bot.modules.federations.services.federation.ChatModel.get_by_tid",
            new=AsyncMock(return_value=user_model),
        ),
        patch(
            "sophie_bot.modules.federations.services.federation.UserInGroupModel.find", return_value=user_in_group_query
        ),
        patch(
            "sophie_bot.modules.federations.services.federation.restrict_ban_user",
            new=AsyncMock(return_value=True),
        ) as mock_restrict_ban_user,
    ):
        banned_count = await FederationService.ban_user_in_federation_chats(federation, ban, user_tid)

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
        patch.object(federation_service_module.ChatModel, "iid", new=MagicMock(), create=True),
        patch("sophie_bot.modules.federations.services.federation.ChatModel.find", return_value=chat_query),
        patch(
            "sophie_bot.modules.federations.services.federation.ChatModel.get_by_tid",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "sophie_bot.modules.federations.services.federation.UserInGroupModel.find",
            return_value=MagicMock(),
        ) as mock_user_in_group_find,
        patch(
            "sophie_bot.modules.federations.services.federation.restrict_ban_user",
            new=AsyncMock(return_value=True),
        ) as mock_restrict_ban_user,
    ):
        banned_count = await FederationService.ban_user_in_federation_chats(federation, ban, user_tid)

    assert banned_count == 0
    mock_user_in_group_find.assert_not_called()
    mock_restrict_ban_user.assert_not_called()
    ban.save.assert_not_called()


@pytest.mark.asyncio
async def test_promote_admin_raises_for_existing_admin_link() -> None:
    user_iid = PydanticObjectId("507f1f77bcf86cd799439041")

    federation = MagicMock()
    federation.admins = [FakeDbRefLink(user_iid)]
    federation.save = AsyncMock()

    with pytest.raises(ValueError, match="already an admin"):
        await FederationService.promote_admin(federation, user_iid)

    federation.save.assert_not_called()


@pytest.mark.asyncio
async def test_demote_admin_removes_matching_admin_link() -> None:
    removed_admin_iid = PydanticObjectId("507f1f77bcf86cd799439051")
    remaining_admin_iid = PydanticObjectId("507f1f77bcf86cd799439052")

    federation = MagicMock()
    federation.admins = [FakeDbRefLink(removed_admin_iid), FakeDbRefLink(remaining_admin_iid)]
    federation.save = AsyncMock()

    await FederationService.demote_admin(federation, removed_admin_iid)

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
        await FederationService.demote_admin(federation, missing_admin_iid)

    federation.save.assert_not_called()
