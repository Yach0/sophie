from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat
from beanie.odm.fields import Link as BeanieLink
from bson import DBRef
from bson.objectid import ObjectId

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.middlewares.connections import ConnectionsMiddleware


def build_private_event_chat(user_tid: int, first_name: str) -> Chat:
    return Chat(id=user_tid, type="private", first_name=first_name)


async def build_private_chat_model(user_tid: int, title: str, username: str | None = None) -> ChatModel:
    chat_model = ChatModel(
        tid=user_tid,
        type=ChatType.private,
        first_name_or_title=title,
        username=username,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    )
    await chat_model.insert()
    return chat_model


async def build_group_chat_model(chat_tid: int, title: str) -> ChatModel:
    group_model = ChatModel(
        tid=chat_tid,
        type=ChatType.supergroup,
        first_name_or_title=title,
        username=None,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    )
    await group_model.insert()
    return group_model


@pytest.mark.asyncio
async def test_connections_middleware_disconnects_dangling_chat_link(db_init: Any) -> None:
    del db_init
    await ChatConnectionModel.delete_all()
    await ChatModel.delete_all()

    user_tid = 2128185818
    stale_chat_iid = ObjectId()
    stale_chat_link = BeanieLink(DBRef("chats", stale_chat_iid), ChatModel)
    fake_connection_model = SimpleNamespace(
        chat=stale_chat_link,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
        save=AsyncMock(),
    )

    middleware = ConnectionsMiddleware()
    event_chat = build_private_event_chat(user_tid=user_tid, first_name="The Untold")
    bot_mock = AsyncMock()
    data: dict[str, Any] = {"event_chat": event_chat, "bot": bot_mock}
    original_get_by_user_tid = ChatConnectionModel.get_by_user_tid
    ChatConnectionModel.get_by_user_tid = AsyncMock(return_value=fake_connection_model)  # type: ignore[method-assign]

    async def mock_handler(_event: Any, payload: dict[str, Any]) -> str:
        assert payload["connection"].is_connected is False
        return "handler_result"

    try:
        result = await middleware(mock_handler, event_chat, data)
    finally:
        ChatConnectionModel.get_by_user_tid = original_get_by_user_tid

    assert result == "handler_result"
    assert fake_connection_model.chat is None
    assert fake_connection_model.expires_at is None
    fake_connection_model.save.assert_awaited_once()
    bot_mock.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_migration_repairs_dangling_connections(db_init: Any) -> None:
    del db_init
    await ChatConnectionModel.delete_all()
    await ChatModel.delete_all()

    user_tid = 111111111
    valid_chat_tid = -100111111111
    stale_chat_iid = ObjectId()
    user_chat = await build_private_chat_model(user_tid=user_tid, title="Cleanup User")
    valid_chat = await build_group_chat_model(chat_tid=valid_chat_tid, title="Cleanup Group")

    connection_model = ChatConnectionModel(
        user=user_chat,
        chat=valid_chat,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
        history=[valid_chat],
    )
    await connection_model.insert()

    connections_collection = ChatConnectionModel.get_pymongo_collection()
    await connections_collection.update_one(
        {"_id": connection_model.id},
        {
            "$set": {
                "chat": DBRef("chats", stale_chat_iid),
                "history": [DBRef("chats", stale_chat_iid), DBRef("chats", valid_chat.iid)],
            }
        },
    )

    migration_module = importlib.import_module(
        "sophie_bot.db.migrations.20260302_090000_cleanup_connections_dangling_chat_links"
    )
    migration_forward = migration_module.Forward()
    await migration_forward.cleanup.run(session=None)

    raw_connection_document = await connections_collection.find_one({"_id": connection_model.id})
    assert raw_connection_document is not None
    assert raw_connection_document.get("chat") is None
    assert raw_connection_document.get("expires_at") is None
    history_references = raw_connection_document.get("history") or []
    history_reference_ids = [history_reference.id for history_reference in history_references]
    assert stale_chat_iid not in history_reference_ids


@pytest.mark.asyncio
async def test_delete_chat_removes_related_connection_records(db_init: Any) -> None:
    del db_init
    await ChatConnectionModel.delete_all()
    await ChatModel.delete_all()

    user_chat = await build_private_chat_model(user_tid=222222222, title="Delete User")
    removable_group = await build_group_chat_model(chat_tid=-100222222222, title="Delete Group")

    connection_model = ChatConnectionModel(
        user=user_chat,
        chat=removable_group,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
        history=[removable_group],
    )
    await connection_model.insert()

    await removable_group.delete_chat()

    reloaded_connection = await ChatConnectionModel.get_by_user_tid(user_chat.tid)
    assert reloaded_connection is None
