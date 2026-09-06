from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.modules.warns.utils import warn_user
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult, StoredAction
from sophie_bot.utils.group_whitelist import add_user_to_group_whitelist
from tests.e2e.helpers import set_feature


@pytest.mark.asyncio
async def test_filter_warn_and_delete_message_warns_user(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000001, title="Filters Warn Group")
    user_wrapper = test_client.create_user(user_id=926000001, first_name="FilterTarget", username="filter_target")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    chat = await ChatModel.get_by_tid(group_chat.id)
    user = await ChatModel.get_by_tid(user_wrapper.user.id)
    assert chat is not None
    assert user is not None

    filter_item = FiltersModel(
        chat=chat.iid,
        handler="spam",
        action=None,
        actions={
            "warn_user": {"reason": "No spam"},
            "delmsg": None,
        },
    )
    await filter_item.insert()

    with patch.object(FiltersModel, "get_filters", AsyncMock(return_value=[filter_item])):
        requests = await test_client.send_message(
            text="this is spam content", from_user=user_wrapper.user, chat=group_chat
        )
    assert requests, "Bot should execute filter actions for matching message"
    assert len(requests) >= 2, "Filter with warn + delete should trigger both delete and warning response"

    warns_count = await WarnModel.find_all().count()
    assert warns_count == 1


@pytest.mark.asyncio
async def test_filter_restrictive_actions_skip_group_whitelisted_user(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    group_chat = ChatFactory.create_group(chat_id=-1002600000010, title="Whitelisted Filters Group")
    user_wrapper = test_client.create_user(user_id=926000010, first_name="Allowed", username="allowed_target")
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None
    filter_item = FiltersModel(
        chat=chat.iid,
        handler="spam",
        action=None,
        actions={"warn_user": {"reason": "No spam"}, "delmsg": None, "mute_user": None},
    )
    await filter_item.insert()
    await add_user_to_group_whitelist(
        group_chat.id,
        user_wrapper.user.id,
        redis=test_client.dispatcher.workflow_data["services"].redis,
    )

    with patch.object(FiltersModel, "get_filters", AsyncMock(return_value=[filter_item])):
        requests = await test_client.send_message(
            text="this is spam content", from_user=user_wrapper.user, chat=group_chat
        )

    assert await WarnModel.find_all().count() == 0
    assert not [
        request
        for request in requests
        if request.request_type in {RequestType.DELETE_MESSAGE, RequestType.RESTRICT_CHAT_MEMBER}
    ]


@pytest.mark.asyncio
async def test_warn_user_executes_each_and_max_actions(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000002, title="Warn Actions Group")
    user_wrapper = test_client.create_user(user_id=926000002, first_name="WarnTarget", username="warn_target")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    chat = await ChatModel.get_by_tid(group_chat.id)
    user = await ChatModel.get_by_tid(user_wrapper.user.id)
    assert chat is not None
    assert user is not None

    settings = await WarnSettingsModel.get_or_create(chat.iid)
    settings.max_warns = 2
    settings.on_each_warn_actions = [StoredAction(name="kick_user", data={})]
    settings.on_max_warn_actions = [
        StoredAction(name="mute_user", data={}),
        StoredAction(name="ban_user", data={}),
    ]
    await settings.save()

    execute_restriction = AsyncMock(
        side_effect=[
            RestrictionResult(
                action=RestrictionAction.KICK,
                applied=True,
            ),
            RestrictionResult(
                action=RestrictionAction.KICK,
                applied=True,
            ),
            RestrictionResult(
                action=RestrictionAction.MUTE,
                applied=True,
            ),
            RestrictionResult(
                action=RestrictionAction.BAN,
                applied=True,
            ),
        ]
    )
    services = test_client.dispatcher.workflow_data["services"]
    with (
        patch.object(WarnSettingsModel, "get_or_create", AsyncMock(return_value=settings)),
        patch.object(WarnModel, "count_user_warns", AsyncMock(side_effect=[1, 2])),
        patch(
            "sophie_bot.modules.warns.utils.execute_restriction",
            execute_restriction,
        ),
    ):
        await warn_user(chat, user, user, "warn #1", services=services)
        await warn_user(chat, user, user, "warn #2", services=services)

    assert [
        call.args[1]
        for call in execute_restriction.await_args_list
    ] == [
        RestrictionAction.KICK,
        RestrictionAction.KICK,
        RestrictionAction.MUTE,
        RestrictionAction.BAN,
    ]
