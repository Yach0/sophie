"""Regression tests: what `/disable` persists must be what the middleware enforces."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.types import Message
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory
from bson import DBRef

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.middlewares.disabling import DisablingMiddleware
from sophie_bot.modules.ai.handlers.translate import AiTranslate

_ADMIN_PERMS_PATCH = "sophie_bot.filters.admin_rights.check_user_admin_permissions"
_MIDDLEWARE_IS_ADMIN_PATCH = "sophie_bot.middlewares.disabling.is_user_admin"

# `/translate` is an alias of `/aitranslate`, and the canonical disable-able name of the handler
# matches neither its first command nor the alias order — the exact shape that used to persist a key
# no middleware would ever enforce.
TRANSLATE_CANONICAL_NAME: str = AiTranslate.aiogram_flag["disableable"].name


async def _persisted_disabled_cmds(chat_db: ChatModel) -> list[str]:
    """Reads the raw persisted keys.

    mongomock cannot resolve the `chat.$id` predicate `DisablingModel.get_disabled` renders, so the
    document is fetched by its DBRef instead.
    """
    doc = await DisablingModel.get_pymongo_collection().find_one({"chat": DBRef("chats", chat_db.iid)})
    return doc["cmds"] if doc else []


async def _translate_handler_reached(chat_db: ChatModel, message: Message, disabled: list[str]) -> bool:
    """Runs the real middleware over the real translate handler for a non-admin."""
    reached = False

    async def handler(event: Any, data: dict[str, Any]) -> None:
        nonlocal reached
        reached = True

    data: dict[str, Any] = {"chat_db": chat_db, "handler": HandlerObject(callback=AiTranslate)}

    with (
        patch(_MIDDLEWARE_IS_ADMIN_PATCH, new=AsyncMock(return_value=False)),
        patch.object(DisablingModel, "get_disabled", new=AsyncMock(return_value=disabled)),
    ):
        await DisablingMiddleware()(handler, message, data)

    return reached


@pytest.mark.asyncio
async def test_disable_persists_the_key_the_middleware_enforces(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002800000010, title="Keyspace Group")
    admin_wrapper = test_client.create_user(user_id=928000010, first_name="Admin", username="keyspace_admin")
    member_wrapper = test_client.create_user(user_id=928000011, first_name="Member", username="keyspace_member")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)

    with patch(_ADMIN_PERMS_PATCH, new=AsyncMock(return_value=True)):
        requests = await test_client.send_command(
            command="disable",
            args="translate",
            from_user=admin_wrapper.user,
            chat=group_chat,
        )

    assert any("Command disabled" in (response.text or "") for response in requests)

    chat_db = await ChatModel.get_by_tid(group_chat.id)
    assert chat_db

    persisted = await _persisted_disabled_cmds(chat_db)
    assert persisted == [TRANSLATE_CANONICAL_NAME]

    message = MessageFactory.create(text="/translate hello", from_user=member_wrapper.user, chat=group_chat)

    assert await _translate_handler_reached(chat_db, message, []), "Handler must run while nothing is disabled"

    with pytest.raises(SkipHandler):
        await _translate_handler_reached(chat_db, message, persisted)


@pytest.mark.asyncio
async def test_disable_accepts_the_canonical_name_of_an_aliased_command(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002800000012, title="Keyspace Alias Group")
    admin_wrapper = test_client.create_user(user_id=928000012, first_name="Admin", username="alias_admin")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)

    with patch(_ADMIN_PERMS_PATCH, new=AsyncMock(return_value=True)):
        await test_client.send_command(
            command="disable",
            args="aitranslate",
            from_user=admin_wrapper.user,
            chat=group_chat,
        )

    chat_db = await ChatModel.get_by_tid(group_chat.id)
    assert chat_db

    assert await _persisted_disabled_cmds(chat_db) == [TRANSLATE_CANONICAL_NAME]


@pytest.mark.asyncio
async def test_enable_reports_a_command_that_is_not_disabled(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002800000013, title="Enable State Group")
    admin_wrapper = test_client.create_user(user_id=928000013, first_name="Admin", username="enable_admin")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)

    with patch(_ADMIN_PERMS_PATCH, new=AsyncMock(return_value=True)):
        requests = await test_client.send_command(
            command="enable",
            args="rules",
            from_user=admin_wrapper.user,
            chat=group_chat,
        )

    texts = [response.text or "" for response in requests]
    assert any("is not disabled" in text for text in texts), texts
    assert not any("already disabled" in text for text in texts), texts
