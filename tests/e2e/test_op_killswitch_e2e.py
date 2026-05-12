from __future__ import annotations

from unittest.mock import patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel


async def _send_op_killswitch(
    test_client: TestClient,
    *,
    chat_tid: int,
    user_tid: int,
    command: str = "op_killswitch",
) -> str:
    group_chat = ChatFactory.create_group(chat_id=chat_tid, title="KillSwitch Test Group")
    user_wrapper = test_client.create_user(user_id=user_tid, first_name="Op", username=f"op_user_{user_tid}")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None

    with patch.object(CONFIG, "operators", [user_wrapper.user.id]):
        requests = await test_client.send_command(
            command=command,
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond"
    return requests[-1].text or ""


@pytest.mark.asyncio
async def test_op_killswitch_no_args_lists_features(test_client: TestClient) -> None:
    response_text = await _send_op_killswitch(
        test_client,
        chat_tid=-1002950000081,
        user_tid=929500081,
    )

    assert "required argument" not in response_text
    assert "Key-values" not in response_text
    assert "ai_chatbot:" in response_text


@pytest.mark.asyncio
async def test_op_killswitch_set_global(test_client: TestClient) -> None:
    response_text = await _send_op_killswitch(
        test_client,
        chat_tid=-1002950000082,
        user_tid=929500082,
        command="op_killswitch ai_chatbot false",
    )

    assert "ai_chatbot</code>: <code>false</code>" in response_text


@pytest.mark.asyncio
async def test_op_killswitch_set_chat_override_current_chat(test_client: TestClient) -> None:
    chat_tid = -1002950000083
    response_text = await _send_op_killswitch(
        test_client,
        chat_tid=chat_tid,
        user_tid=929500083,
        command="op_killswitch ^chat ai_chatbot true",
    )

    assert f"for chat {chat_tid}:" in response_text


@pytest.mark.asyncio
async def test_op_killswitch_invalid_feature(test_client: TestClient) -> None:
    response_text = await _send_op_killswitch(
        test_client,
        chat_tid=-1002950000084,
        user_tid=929500084,
        command="op_killswitch nonexistent_feature true",
    )

    assert "Unknown feature" in response_text
    assert "nonexistent_feature" in response_text


@pytest.mark.asyncio
async def test_op_killswitch_partial_args(test_client: TestClient) -> None:
    response_text = await _send_op_killswitch(
        test_client,
        chat_tid=-1002950000085,
        user_tid=929500085,
        command="op_killswitch ai_chatbot",
    )

    assert "Usage: /op_killswitch" in response_text
    assert "Allowed features:" in response_text
