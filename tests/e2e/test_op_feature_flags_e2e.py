from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.feature_flag import FeatureFlagOverride


@pytest.fixture(autouse=True)
async def _reset_feature_flag_overrides(db_init: object) -> AsyncGenerator[None, None]:
    await FeatureFlagOverride.get_pymongo_collection().delete_many({})
    yield
    await FeatureFlagOverride.get_pymongo_collection().delete_many({})


async def _send_op_ff(
    test_client: TestClient,
    *,
    chat_tid: int,
    user_tid: int,
    command: str = "op_ff",
) -> str:
    group_chat = ChatFactory.create_group(chat_id=chat_tid, title="Feature Flags Test Group")
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
async def test_op_ff_no_args_reports_no_changed_flags(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950000081,
        user_tid=929500081,
    )

    assert "required argument" not in response_text
    assert "Key-values" not in response_text
    assert "No changed feature flags are set." in response_text
    assert "<blockquote expandable>All feature flags" in response_text
    assert "Args: <code>^chat[=&lt;chat_id&gt;]" in response_text


@pytest.mark.asyncio
async def test_op_ff_alias_lists_features(test_client: TestClient) -> None:
    await _send_op_ff(
        test_client,
        chat_tid=-1002950000086,
        user_tid=929500086,
        command="op_ff ai_proactive_replies true",
    )
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950000086,
        user_tid=929500086,
    )

    assert "ai_proactive_replies: false -&gt; true ✅" in response_text
    assert "<blockquote expandable>All feature flags" in response_text


@pytest.mark.asyncio
async def test_op_ff_feature_name_shows_full_value(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950000087,
        user_tid=929500087,
        command="op_ff ai_proactive_replies_prompt",
    )

    assert "ai_proactive_replies_prompt" in response_text
    assert "Be very conservative" in response_text
    assert "..." not in response_text


@pytest.mark.asyncio
async def test_op_ff_set_global(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950000082,
        user_tid=929500082,
        command="op_ff ai_chatbot false",
    )

    assert "ai_chatbot</code>: <code>false</code>" in response_text


@pytest.mark.asyncio
async def test_op_ff_set_rollout(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950088,
        user_tid=929500088,
        command="op_ff ^rollout=10 ai_proactive_replies true",
    )

    assert "ai_proactive_replies</code>: <code>rollout 10% -&gt; true</code>" in response_text

    list_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950088,
        user_tid=929500088,
    )

    assert "ai_proactive_replies: false -&gt; true ✅ (rollout 10% -&gt; true)" in list_text
    assert "Args: <code>^chat[=&lt;chat_id&gt;]" in list_text


@pytest.mark.asyncio
async def test_op_ff_set_timed_rollout(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950089,
        user_tid=929500089,
        command="op_ff ^days=7 ai_proactive_replies true",
    )

    assert "ai_proactive_replies</code>: <code>rollout 0%/100% over 7d -&gt; true</code>" in response_text


@pytest.mark.asyncio
async def test_op_ff_bump_rollout(test_client: TestClient) -> None:
    await _send_op_ff(
        test_client,
        chat_tid=-1002950090,
        user_tid=929500090,
        command="op_ff ^rollout=10 ai_proactive_replies true",
    )
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950090,
        user_tid=929500090,
        command="op_ff ^rollout_bump=15 ai_proactive_replies",
    )

    assert "ai_proactive_replies</code>: <code>rollout 25% -&gt; true</code>" in response_text


@pytest.mark.asyncio
async def test_op_ff_set_chat_override_current_chat(test_client: TestClient) -> None:
    chat_tid = -1002950000083
    response_text = await _send_op_ff(
        test_client,
        chat_tid=chat_tid,
        user_tid=929500083,
        command="op_ff ^chat ai_chatbot true",
    )

    assert f"for chat {chat_tid}:" in response_text


@pytest.mark.asyncio
async def test_op_ff_lists_chat_override_sources(test_client: TestClient) -> None:
    manual_chat_tid = -1002950000091
    rollout_chat_tid = -1002950000092

    await _send_op_ff(
        test_client,
        chat_tid=manual_chat_tid,
        user_tid=929500091,
        command="op_ff ^chat ai_chatbot true",
    )
    await _send_op_ff(
        test_client,
        chat_tid=rollout_chat_tid,
        user_tid=929500092,
        command="op_ff ^rollout=100 ai_proactive_replies true",
    )
    await _send_op_ff(
        test_client,
        chat_tid=rollout_chat_tid,
        user_tid=929500092,
        command="op_ff ^chat ai_proactive_replies",
    )

    response_text = await _send_op_ff(
        test_client,
        chat_tid=manual_chat_tid,
        user_tid=929500091,
        command="op_ff ^chat_overrides",
    )

    assert "<blockquote expandable>Manual per-chat overrides" in response_text
    assert f"{manual_chat_tid}: ai_chatbot -&gt; true ✅ (manual)" in response_text
    assert "<blockquote expandable>Rollout-created per-chat overrides" in response_text
    assert f"{rollout_chat_tid}: ai_proactive_replies -&gt; true ✅ (rollout)" in response_text


@pytest.mark.asyncio
async def test_op_ff_invalid_feature(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950000084,
        user_tid=929500084,
        command="op_ff nonexistent_feature true",
    )

    assert "Unknown feature" in response_text
    assert "nonexistent_feature" in response_text


@pytest.mark.asyncio
async def test_op_ff_rejects_invalid_model_flag_value(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950000093,
        user_tid=929500093,
        command="op_ff ai_chatbot_model garbage",
    )

    assert "Invalid value" in response_text
    assert "ai_chatbot_model" in response_text
    assert await FeatureFlagOverride.find_one(FeatureFlagOverride.feature == "ai_chatbot_model") is None


@pytest.mark.asyncio
async def test_op_ff_partial_args_shows_feature_value(test_client: TestClient) -> None:
    response_text = await _send_op_ff(
        test_client,
        chat_tid=-1002950000085,
        user_tid=929500085,
        command="op_ff ai_chatbot",
    )

    assert "ai_chatbot</code>: <code>" in response_text
