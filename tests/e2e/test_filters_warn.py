from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.db.models.warns import WarnModel


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
