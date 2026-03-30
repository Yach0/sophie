from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.modules.ai.json_schemas.filter_suggestions import (
    AIFilterSuggestion,
    AIFilterSuggestionsResponse,
)


@pytest.mark.asyncio
async def test_aiaddfilter_returns_suggestions(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000004, title="AI Filter Suggestions")
    admin_wrapper = test_client.create_user(user_id=926000004, first_name="Admin", username="admin_user")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)

    ai_result = SimpleNamespace(
        output=AIFilterSuggestionsResponse(
            suggestions=[
                AIFilterSuggestion(
                    handler="re:crypto|btc|bitcoin|blockchain",
                    description="Matches common crypto-related words",
                    note="Recommended",
                    recommended=True,
                ),
                AIFilterSuggestion(
                    handler="word:crypto scam",
                    description="Matches the whole phrase",
                    note="Only matches whole words, not partial",
                    recommended=False,
                ),
                AIFilterSuggestion(
                    handler="ai:messages promoting cryptocurrency or crypto scams",
                    description="Uses semantic matching",
                    note="Uses AI quota",
                    recommended=False,
                ),
            ]
        ),
        usage=SimpleNamespace(total_tokens=0, request_tokens=0, response_tokens=0),
    )

    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.ai.filters.ai_enabled.AIEnabledFilter.get_status", AsyncMock(return_value=True)),
        patch(
            "sophie_bot.modules.ai.filters.quota.check_quota",
            AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ),
        patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)),
        patch(
            "sophie_bot.modules.ai.handlers.ai_addfilter.get_chat_default_model",
            AsyncMock(return_value=SimpleNamespace(model_name="test-model")),
        ),
        patch(
            "sophie_bot.modules.ai.handlers.ai_addfilter.new_ai_generate_schema_with_result",
            AsyncMock(return_value=ai_result),
        ),
    ):
        requests = await test_client.send_command(
            command="aiaddfilter",
            from_user=admin_wrapper.user,
            chat=group_chat,
            args="block crypto spam",
        )

    assert requests, "Bot should respond with AI-generated filter suggestions"
    response_text = requests[-1].text or ""
    assert "AI Filter Suggestions" in response_text
    assert "re:crypto|btc|bitcoin|blockchain" in response_text
    assert "word:crypto scam" in response_text
    assert "ai:messages promoting cryptocurrency or crypto scams" in response_text
    assert "Use /addfilter &lt;handler&gt; to create the filter." in response_text
    assert "For example, <code>/addfilter re:crypto|btc|bitcoin|blockchain</code>" in response_text


@pytest.mark.asyncio
async def test_aiaddfilter_returns_generic_error_when_ai_fails(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002600000005, title="AI Filter Failure")
    admin_wrapper = test_client.create_user(user_id=926000005, first_name="Admin", username="admin_user_two")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)

    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.ai.filters.ai_enabled.AIEnabledFilter.get_status", AsyncMock(return_value=True)),
        patch(
            "sophie_bot.modules.ai.filters.quota.check_quota",
            AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ),
        patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)),
        patch(
            "sophie_bot.modules.ai.handlers.ai_addfilter.get_chat_default_model",
            AsyncMock(return_value=SimpleNamespace(model_name="test-model")),
        ),
        patch(
            "sophie_bot.modules.ai.handlers.ai_addfilter.new_ai_generate_schema_with_result",
            AsyncMock(side_effect=TimeoutError),
        ),
    ):
        requests = await test_client.send_command(
            command="aiaddfilter",
            from_user=admin_wrapper.user,
            chat=group_chat,
            args="block crypto spam",
        )

    assert requests, "Bot should reply with a generic error when AI generation fails"
    response_text = requests[-1].text or ""
    assert "Could not generate suggestions." in response_text
