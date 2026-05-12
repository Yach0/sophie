from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils.cache_messages import cache_message


@pytest.mark.asyncio
async def test_op_debug_responds_to_operator(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000010, title="Op Debug Group")
    operator_wrapper = test_client.create_user(user_id=929500010, first_name="Operator", username="op_user_10")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /op_debug"
    response_text = requests[-1].text or ""
    assert "Operator Debug" in response_text


@pytest.mark.asyncio
async def test_op_debug_rejected_for_non_operator(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000011, title="Op Debug Non Op Group")
    user_wrapper = test_client.create_user(user_id=929500011, first_name="User", username="non_op_user_11")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", []):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    response_text = requests[-1].text if requests else ""
    assert "Operator Debug" not in response_text


@pytest.mark.asyncio
async def test_op_debug_includes_chat_history(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000012, title="Op Debug History Group")
    operator_wrapper = test_client.create_user(user_id=929500012, first_name="Operator", username="op_user_12")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None

    created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await cache_message("hello", group_chat.id, 123, 1, created_at, "user_123")
    await cache_message("hi there", group_chat.id, CONFIG.bot_id, 2, created_at + timedelta(minutes=1), "sophie")
    await cache_message("how are you", group_chat.id, 123, 3, created_at + timedelta(minutes=2), "user_123")

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /op_debug"
    response_text = requests[-1].text or ""
    assert "Chat History" in response_text
    assert "hello" in response_text
    assert "hi there" in response_text
    assert "how are you" in response_text


@pytest.mark.asyncio
async def test_op_debug_includes_operator_notes_with_reply(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000013, title="Op Debug Notes Group")
    operator_wrapper = test_client.create_user(user_id=929500013, first_name="Operator", username="op_user_13")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug bot is crashing on /ai command",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /op_debug"
    response_text = requests[-1].text or ""
    assert "Operator Notes" in response_text
    assert "bot is crashing on /ai command" in response_text


@pytest.mark.asyncio
async def test_op_debug_shows_empty_chat_history_when_no_cache(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000014, title="Op Debug Empty History Group")
    operator_wrapper = test_client.create_user(user_id=929500014, first_name="Operator", username="op_user_14")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_debug",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /op_debug"
    response_text = requests[-1].text or ""
    assert "No cached messages found" in response_text
