"""Unit tests for CommunityBanService eligibility validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.communities.exceptions import CommunityBanValidationError
from sophie_bot.modules.communities.services import CommunityBanService
from sophie_bot.modules.communities.services import ban as ban_service_module
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult


def test_validate_allows_regular_user() -> None:
    # Should not raise for an ordinary target/banner pair.
    CommunityBanService.validate_ban_eligibility(target_user_tid=111, banner_user_tid=222)


def test_validate_blocks_self_ban() -> None:
    with pytest.raises(CommunityBanValidationError):
        CommunityBanService.validate_ban_eligibility(target_user_tid=111, banner_user_tid=111)


def test_validate_blocks_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "operators", [999])
    with pytest.raises(CommunityBanValidationError):
        CommunityBanService.validate_ban_eligibility(target_user_tid=999, banner_user_tid=222)


def test_validate_blocks_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    # bot_id is a property on the Config class, so patch it there.
    monkeypatch.setattr(type(CONFIG), "bot_id", property(lambda _self: 42))
    with pytest.raises(CommunityBanValidationError):
        CommunityBanService.validate_ban_eligibility(target_user_tid=42, banner_user_tid=222)


@pytest.mark.asyncio
async def test_ban_user_in_community_chats_ignores_channel(test_services: object) -> None:
    user_tid = 111
    user_iid = PydanticObjectId("507f1f77bcf86cd799439101")
    group_iid = PydanticObjectId("507f1f77bcf86cd799439102")
    channel_iid = PydanticObjectId("507f1f77bcf86cd799439103")
    group = MagicMock(iid=group_iid, tid=-100100, type=ChatType.supergroup)
    channel = MagicMock(iid=channel_iid, tid=-100200, type=ChatType.channel)
    user = MagicMock(iid=user_iid)
    group_membership = MagicMock()
    group_membership.group.to_ref.return_value = group_iid
    channel_membership = MagicMock()
    channel_membership.group.to_ref.return_value = channel_iid
    chat_query = MagicMock(to_list=AsyncMock(return_value=[group, channel]))
    membership_query = MagicMock(to_list=AsyncMock(return_value=[group_membership, channel_membership]))
    ban = MagicMock(banned_chats=[], save=AsyncMock())

    with (
        patch.object(ban_service_module.ChatModel, "community_tid", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "user", new=MagicMock(), create=True),
        patch.object(ban_service_module.UserInGroupModel, "group", new=MagicMock(), create=True),
        patch("sophie_bot.modules.communities.services.ban.ChatModel.find", return_value=chat_query),
        patch(
            "sophie_bot.modules.communities.services.ban.ChatModel.get_by_tid",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "sophie_bot.modules.communities.services.ban.UserInGroupModel.find",
            return_value=membership_query,
        ),
        patch(
            "sophie_bot.modules.communities.services.ban.execute_restriction",
            new=AsyncMock(return_value=RestrictionResult(action=RestrictionAction.BAN, applied=True)),
        ) as execute_restriction,
    ):
        count = await CommunityBanService.ban_user_in_community_chats(555, ban, user_tid, bot=test_services.bot)

    assert count == 1
    execute_restriction.assert_awaited_once_with(test_services.bot, RestrictionAction.BAN, group.tid, user_tid)
    assert ban.banned_chats == [group]
