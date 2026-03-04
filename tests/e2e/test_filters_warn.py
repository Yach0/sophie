from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FilterActionType, FiltersModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.modules.warns.utils import warn_user


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
    settings.on_each_warn_actions = [FilterActionType(name="kick_user", data={})]
    settings.on_max_warn_actions = [
        FilterActionType(name="mute_user", data={}),
        FilterActionType(name="ban_user", data={}),
    ]
    await settings.save()

    with (
        patch.object(WarnSettingsModel, "get_or_create", AsyncMock(return_value=settings)),
        patch.object(WarnModel, "count_user_warns", AsyncMock(side_effect=[1, 2])),
        patch("sophie_bot.modules.warns.utils.kick_user", AsyncMock(return_value=True)) as kick_user_mock,
        patch("sophie_bot.modules.warns.utils.mute_user", AsyncMock(return_value=True)) as mute_user_mock,
        patch("sophie_bot.modules.warns.utils.ban_user", AsyncMock(return_value=True)) as ban_user_mock,
    ):
        await warn_user(chat, user, user, "warn #1")
        await warn_user(chat, user, user, "warn #2")

    assert kick_user_mock.await_count == 2
    assert mute_user_mock.await_count == 1
    assert ban_user_mock.await_count == 1
