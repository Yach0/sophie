from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.schedules.generate_chat_summaries import GenerateChatSummaries


@pytest.mark.asyncio
async def test_op_regenerate_chat_summary_forces_generation(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000003, title="Op Regenerate Summarize Group")
    operator_wrapper = test_client.create_user(user_id=929500003, first_name="Operator", username="op_user_3")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None
    summary_date = datetime.now(UTC).date()

    with (
        patch.object(CONFIG, "operators", [operator_wrapper.user.id]),
        patch.object(
            GenerateChatSummaries,
            "process_chat",
            AsyncMock(),
        ) as process_chat,
    ):
        requests = await test_client.send_command(
            command="op_regenerate_chat_summary",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert process_chat.await_count == 1
    process_chat_chat, process_chat_date = process_chat.await_args.args[:2]
    assert process_chat_chat.tid == group_chat.id
    assert process_chat_date == summary_date
    assert process_chat.await_args.kwargs == {"force": True, "target_chat_tid": group_chat.id}
    assert requests, "Bot should respond to /op_regenerate_chat_summary"
    response_text = requests[-1].text or ""
    assert "Chat summary regenerated" in response_text
