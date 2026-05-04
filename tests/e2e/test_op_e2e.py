from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models import AIChatSummaryLine, AIChatSummaryModel, ChatModel
from sophie_bot.modules.ai.schedules.generate_chat_summaries import GenerateChatSummaries
from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages_between


@pytest.mark.asyncio
async def test_op_test_sumarrize_history_seeds_chat_cache(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000001, title="Op Test Summarize Group")
    operator_wrapper = test_client.create_user(user_id=929500001, first_name="Operator", username="op_user")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]):
        requests = await test_client.send_command(
            command="op_test_sumarrize_history",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /op_test_sumarrize_history"
    response_text = requests[-1].text or ""
    assert "Test summarize history added" in response_text
    assert "Messages added" in response_text

    summary_date = datetime.now(timezone.utc).date() - timedelta(days=1)
    window_start = datetime.combine(summary_date, time.min, tzinfo=timezone.utc)
    window_end = datetime.combine(summary_date, time.max, tzinfo=timezone.utc)
    cached_messages = await get_cached_messages_between(group_chat.id, window_start, window_end)

    assert len(cached_messages) == 35
    assert cached_messages[0].username == "erin"
    assert cached_messages[0].text == "Morning standup: let's review yesterday's summary generation output first."
    assert cached_messages[-1].username == "carol"
    assert (
        cached_messages[-1].text
        == "Finally, we can hand the tester a command that populates enough history for a useful summary."
    )


@pytest.mark.asyncio
async def test_op_preview_chat_summary_renders_stored_summary(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000002, title="Op Preview Summarize Group")
    operator_wrapper = test_client.create_user(user_id=929500002, first_name="Operator", username="op_user_2")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    preview_summary = SimpleNamespace(
        overview="General overview",
        lines=[
            AIChatSummaryLine(
                emoji="💡",
                title="Topic",
                first_message_id=100,
                first_message_at=datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc),
                usernames=["alice", "bob"],
                source_excerpt="Representative snippet",
            )
        ],
    )

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]), patch.object(
        AIChatSummaryModel,
        "get_for_date",
        AsyncMock(return_value=preview_summary),
    ):
        requests = await test_client.send_command(
            command="op_preview_chat_summary",
            from_user=operator_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /op_preview_chat_summary"
    response_text = requests[-1].text or ""
    assert "General overview" in response_text
    assert "Topic" in response_text


@pytest.mark.asyncio
async def test_op_regenerate_chat_summary_forces_generation(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002950000003, title="Op Regenerate Summarize Group")
    operator_wrapper = test_client.create_user(user_id=929500003, first_name="Operator", username="op_user_3")

    await test_client.send_message(text="init", from_user=operator_wrapper.user, chat=group_chat)
    chat = await ChatModel.get_by_tid(group_chat.id)
    assert chat is not None
    summary_date = datetime.now(timezone.utc).date()

    with patch.object(CONFIG, "operators", [operator_wrapper.user.id]), patch.object(
        GenerateChatSummaries,
        "process_chat",
        AsyncMock(),
    ) as process_chat:
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
