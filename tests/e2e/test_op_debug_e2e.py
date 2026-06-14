from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils.cache_messages import cache_message


def _join_response_texts(requests: list) -> str:
    return "\n".join(request.text or "" for request in requests)


@pytest.mark.asyncio
async def test_op_debug_responds_to_operator(test_client: TestClient) -> None:
    operator_wrapper = test_client.create_user(user_id=929500010, first_name="Operator", username="op_user_10")
    private_chat = ChatFactory.create_private(chat_id=929500010, first_name="Operator", username="op_user_10")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=private_chat)
    chat = await ChatModel.get_by_tid(private_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=private_chat,
        )

    assert requests, "Bot should respond to /op_debug in private chat"
    response_text = requests[-1].text or ""
    assert "Operator Debug" in response_text


@pytest.mark.asyncio
async def test_op_debug_rejected_in_group(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000011, title="Op Debug Group")
    operator_wrapper = test_client.create_user(user_id=929500011, first_name="Operator", username="op_user_11")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    response_text = requests[-1].text if requests else ""
    assert "Operator Debug" not in response_text, "op_debug must not expose output in group chats"


@pytest.mark.asyncio
async def test_op_debug_rejected_for_non_operator(test_client: TestClient) -> None:
    operator_wrapper = test_client.create_user(user_id=929500019, first_name="User", username="non_op_user_19")
    private_chat = ChatFactory.create_private(chat_id=929500019, first_name="User", username="non_op_user_19")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=private_chat)

    with patch.object(CONFIG, "operators", []):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=private_chat,
        )

    response_text = requests[-1].text if requests else ""
    assert "Operator Debug" not in response_text


@pytest.mark.asyncio
async def test_op_debug_includes_chat_history(test_client: TestClient) -> None:
    operator_wrapper = test_client.create_user(user_id=929500012, first_name="Operator", username="op_user_12")
    private_chat = ChatFactory.create_private(chat_id=929500012, first_name="Operator", username="op_user_12")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=private_chat)
    chat = await ChatModel.get_by_tid(private_chat.id)
    assert chat is not None

    created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await cache_message("hello", private_chat.id, 123, 1, created_at, "user_123")
    await cache_message("hi there", private_chat.id, CONFIG.bot_id, 2, created_at + timedelta(minutes=1), "sophie")
    await cache_message("how are you", private_chat.id, 123, 3, created_at + timedelta(minutes=2), "user_123")

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=private_chat,
        )

    assert requests, "Bot should respond to /op_debug"
    response_text = _join_response_texts(requests)
    assert "Chat History" in response_text
    assert "hello" in response_text
    assert "hi there" in response_text
    assert "how are you" in response_text


@pytest.mark.asyncio
async def test_op_debug_includes_operator_notes_with_reply(test_client: TestClient) -> None:
    operator_wrapper = test_client.create_user(user_id=929500013, first_name="Operator", username="op_user_13")
    private_chat = ChatFactory.create_private(chat_id=929500013, first_name="Operator", username="op_user_13")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=private_chat)
    chat = await ChatModel.get_by_tid(private_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug bot is crashing on /ai command",
            from_user=operator_wrapper.user,
            chat=private_chat,
        )

    assert requests, "Bot should respond to /op_debug"
    response_text = requests[-1].text or ""
    assert "Operator Notes" in response_text
    assert "bot is crashing on /ai command" in response_text


@pytest.mark.asyncio
async def test_op_debug_shows_empty_chat_history_when_no_cache(test_client: TestClient) -> None:
    operator_wrapper = test_client.create_user(user_id=929500014, first_name="Operator", username="op_user_14")
    private_chat = ChatFactory.create_private(chat_id=929500014, first_name="Operator", username="op_user_14")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=private_chat)
    chat = await ChatModel.get_by_tid(private_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=private_chat,
        )

    assert requests, "Bot should respond to /op_debug"
    response_text = _join_response_texts(requests)
    assert "No cached messages found" in response_text
