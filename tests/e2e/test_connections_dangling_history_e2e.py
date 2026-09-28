"""Connection picker regression for deleted chats in saved history."""

from datetime import UTC, datetime

import pytest
from aiogram.types import InlineKeyboardMarkup
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.modules.connections.handlers.connect_dm import ConnectToChatCb
from tests.e2e.helpers import create_test_user_and_group, next_group_id


@pytest.mark.asyncio
async def test_connect_history_removes_deleted_chat_and_keeps_surviving_choice(test_client: TestClient) -> None:
    admin, group, user_model = await create_test_user_and_group(test_client, group_title="Surviving group")
    surviving_chat = await ChatModel.get_by_tid(group.id)
    assert surviving_chat is not None
    deleted_chat = await ChatModel(
        tid=next_group_id(),
        type=ChatType.supergroup,
        first_name_or_title="Deleted group",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()
    await ChatConnectionModel(user=user_model, history=[surviving_chat, deleted_chat]).insert()
    await deleted_chat.delete()

    requests = await test_client.send_command(command="connect", from_user=admin)

    replies = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert len(replies) == 1
    assert replies[0].reply_markup is not None
    markup = InlineKeyboardMarkup.model_validate(replies[0].reply_markup)
    assert [
        (button.text, button.callback_data) for row in markup.inline_keyboard for button in row
    ] == [("Surviving group", ConnectToChatCb(chat_id=group.id).pack())]

    connection = await ChatConnectionModel.get_by_user_tid(admin.id)
    assert connection is not None
    assert [link.to_ref().id for link in connection.history] == [surviving_chat.iid]

    # The invalid choice must not return on subsequent reads of persisted history.
    again = await test_client.send_command(command="connect", from_user=admin)
    assert again[-1].reply_markup == replies[0].reply_markup
