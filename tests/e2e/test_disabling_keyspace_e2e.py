"""Regression tests: what `/disable` persists must be what the middleware enforces."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.types import Message
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.middlewares.disabling import DisablingMiddleware
from sophie_bot.modules.ai.handlers.translate import AiTranslate
from tests.e2e.helpers import grant_admin

# `/translate` is an alias of `/aitranslate`, and the canonical disable-able name of the handler
# matches neither its first command nor the alias order — the exact shape that used to persist a key
# no middleware would ever enforce.
TRANSLATE_CANONICAL_NAME: str = AiTranslate.aiogram_flag["disableable"].name


async def _translate_handler_reached(chat_db: ChatModel, message: Message) -> bool:
    """Runs the real middleware over the real translate handler for a non-admin."""
    reached = False

    async def handler(event: Any, data: dict[str, Any]) -> None:
        nonlocal reached
        reached = True

    data: dict[str, Any] = {"chat_db": chat_db, "handler": HandlerObject(callback=AiTranslate)}
    await DisablingMiddleware()(handler, message, data)

    return reached


@pytest.mark.asyncio
async def test_disable_persists_the_key_the_middleware_enforces(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002800000010, title="Keyspace Group")
    admin_wrapper = test_client.create_user(user_id=928000010, first_name="Admin", username="keyspace_admin")
    member_wrapper = test_client.create_user(user_id=928000011, first_name="Member", username="keyspace_member")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    chat_db = await ChatModel.get_by_tid(group_chat.id)
    assert chat_db

    message = MessageFactory.create(text="/translate hello", from_user=member_wrapper.user, chat=group_chat)
    assert await _translate_handler_reached(chat_db, message), "Handler must run while nothing is disabled"

    requests = await test_client.send_command(
        command="disable",
        args="translate",
        from_user=admin_wrapper.user,
        chat=group_chat,
    )
    assert any("Command disabled" in (response.text or "") for response in requests)
    assert await DisablingModel.get_disabled(chat_db.iid) == [TRANSLATE_CANONICAL_NAME]

    with pytest.raises(SkipHandler):
        await _translate_handler_reached(chat_db, message)


@pytest.mark.asyncio
async def test_disable_accepts_the_canonical_name_of_an_aliased_command(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002800000012, title="Keyspace Alias Group")
    admin_wrapper = test_client.create_user(user_id=928000012, first_name="Admin", username="alias_admin")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    await test_client.send_command(
        command="disable",
        args="aitranslate",
        from_user=admin_wrapper.user,
        chat=group_chat,
    )

    chat_db = await ChatModel.get_by_tid(group_chat.id)
    assert chat_db

    assert await DisablingModel.get_disabled(chat_db.iid) == [TRANSLATE_CANONICAL_NAME]


@pytest.mark.asyncio
async def test_enable_reports_a_command_that_is_not_disabled(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002800000013, title="Enable State Group")
    admin_wrapper = test_client.create_user(user_id=928000013, first_name="Admin", username="enable_admin")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await grant_admin(group_chat.id, admin_wrapper.user.id)

    requests = await test_client.send_command(
        command="enable",
        args="rules",
        from_user=admin_wrapper.user,
        chat=group_chat,
    )

    texts = [response.text or "" for response in requests]
    assert any("is not disabled" in text for text in texts), texts
    assert not any("already disabled" in text for text in texts), texts
