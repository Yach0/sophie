from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.types import User
from beanie import PydanticObjectId

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.connections.handlers import connect_dm
from sophie_bot.modules.connections.handlers.connect_dm import ConnectCallback, ConnectDMCmd

USER_TID = 5001
CHAT_A_TID = -1005001
CHAT_B_TID = -1005002


async def _insert_chat(tid: int, chat_type: ChatType, title: str) -> ChatModel:
    """Insert and re-fetch: Beanie assigns `_id` on insert, so the in-memory `iid` is stale until reloaded."""
    await ChatModel(
        tid=tid,
        type=chat_type,
        first_name_or_title=title,
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    ).insert()

    chat = await ChatModel.get_by_tid(tid)
    assert chat is not None
    return chat


async def _build_scenario() -> tuple[ChatModel, ChatModel, ChatModel]:
    await ChatModel.delete_all()
    user = await _insert_chat(USER_TID, ChatType.private, "Connector")
    chat_a = await _insert_chat(CHAT_A_TID, ChatType.supergroup, "Chat A")
    chat_b = await _insert_chat(CHAT_B_TID, ChatType.supergroup, "Chat B")
    return user, chat_a, chat_b


def _connected_to(chat: ChatModel) -> ChatConnection:
    """What ConnectionsMiddleware puts in data['connection'] for a connected user: the CONNECTED CHAT."""
    return ChatConnection(
        type=chat.type,
        is_connected=True,
        tid=chat.tid,
        title=chat.first_name_or_title,
        db_model=chat,
    )


@pytest.fixture
def permission_probe(monkeypatch: pytest.MonkeyPatch) -> list[tuple[PydanticObjectId, PydanticObjectId]]:
    """Records (chat_iid, user_iid) and grants access only to the user recorded in the user slot."""
    calls: list[tuple[PydanticObjectId, PydanticObjectId]] = []

    async def _check(chat_iid: PydanticObjectId, user_iid: PydanticObjectId) -> bool:
        calls.append((chat_iid, user_iid))
        user = await ChatModel.get_by_iid(user_iid)
        # Chat B only admits its admin: the user, never another chat.
        return user is not None and user.tid == USER_TID

    monkeypatch.setattr(connect_dm, "check_connection_permissions", _check)
    monkeypatch.setattr(connect_dm, "set_connected_chat", AsyncMock())
    return calls


@pytest.mark.asyncio
async def test_connect_cmd_checks_permissions_for_the_user_not_the_connected_chat(
    db_init: Any, permission_probe: list[tuple[PydanticObjectId, PydanticObjectId]]
) -> None:
    del db_init
    user, chat_a, chat_b = await _build_scenario()

    event = SimpleNamespace(from_user=User(id=USER_TID, is_bot=False, first_name="Connector"), reply=AsyncMock())
    handler = ConnectDMCmd(event, user_db=user, connection=_connected_to(chat_a), chat=chat_b)

    await handler.handle()

    assert permission_probe == [(chat_b.iid, user.iid)]
    connect_dm.set_connected_chat.assert_awaited_once_with(USER_TID, CHAT_B_TID)

    replies = [call.args[0] for call in event.reply.await_args_list]
    assert not any("not allowed to connect" in reply for reply in replies), replies


@pytest.mark.asyncio
async def test_connect_callback_checks_permissions_for_the_user_not_the_connected_chat(
    db_init: Any, permission_probe: list[tuple[PydanticObjectId, PydanticObjectId]]
) -> None:
    del db_init
    user, chat_a, chat_b = await _build_scenario()

    event = SimpleNamespace(
        from_user=User(id=USER_TID, is_bot=False, first_name="Connector"),
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )
    handler = ConnectCallback(
        event,
        user_db=user,
        connection=_connected_to(chat_a),
        callback_data=SimpleNamespace(chat_id=CHAT_B_TID),
    )

    await handler.handle()

    assert permission_probe == [(chat_b.iid, user.iid)]
    connect_dm.set_connected_chat.assert_awaited_once_with(USER_TID, CHAT_B_TID)

    alerts = [call.args[0] for call in event.answer.await_args_list]
    assert not any("not allowed to connect" in alert for alert in alerts), alerts
