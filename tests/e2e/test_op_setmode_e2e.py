from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.beta import BetaModeModel, PreferredMode


@pytest.fixture(autouse=True)
async def _reset_beta_modes(db_init: object) -> AsyncGenerator[None, None]:
    await BetaModeModel.get_pymongo_collection().delete_many({})
    yield
    await BetaModeModel.get_pymongo_collection().delete_many({})


async def _send_op_setmode(
    test_client: TestClient,
    *,
    chat_tid: int,
    user_tid: int,
    command: str = "op_setmode",
) -> str:
    group_chat = ChatFactory.create_group(chat_id=chat_tid, title="Set Mode Test Group")
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
    return "\n".join(request.text or "" for request in requests)


@pytest.mark.asyncio
async def test_op_setmode_sets_preferred_mode(test_client: TestClient) -> None:
    chat_tid = -1002960000001
    response_text = await _send_op_setmode(
        test_client,
        chat_tid=chat_tid,
        user_tid=929600001,
        command="op_setmode latest",
    )

    assert "Preferred mode changed" in response_text
    assert "Latest" in response_text

    chat = await ChatModel.get_by_tid(chat_tid)
    assert chat is not None
    beta_state = await BetaModeModel.get_by_chat_iid(chat.iid)
    assert beta_state is not None
    assert beta_state.preferred_mode == PreferredMode.beta


@pytest.mark.asyncio
async def test_op_setmode_no_args_shows_state(test_client: TestClient) -> None:
    response_text = await _send_op_setmode(
        test_client,
        chat_tid=-1002960000002,
        user_tid=929600002,
    )

    assert "Mode information" in response_text
    assert "Preferred mode" in response_text


@pytest.mark.asyncio
async def test_op_setmode_chat_arg_targets_chat(test_client: TestClient) -> None:
    chat_tid = -1002960000003
    response_text = await _send_op_setmode(
        test_client,
        chat_tid=chat_tid,
        user_tid=929600003,
        command=f"op_setmode ^chat={chat_tid} old",
    )

    assert "Preferred mode changed" in response_text
    assert str(chat_tid) in response_text

    chat = await ChatModel.get_by_tid(chat_tid)
    assert chat is not None
    beta_state = await BetaModeModel.get_by_chat_iid(chat.iid)
    assert beta_state is not None
    assert beta_state.preferred_mode == PreferredMode.stable


@pytest.mark.asyncio
async def test_op_setmode_unknown_chat_reports_not_found(test_client: TestClient) -> None:
    response_text = await _send_op_setmode(
        test_client,
        chat_tid=-1002960000004,
        user_tid=929600004,
        command="op_setmode ^chat=-100299999999 latest",
    )

    assert "not found" in response_text
