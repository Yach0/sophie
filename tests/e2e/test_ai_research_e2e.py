from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.modules.ai.json_schemas.research import ResearchFinalResponse, ResearchSource
from sophie_bot.modules.ai.utils.research import ResearchWorkflowResult
from sophie_bot.utils.feature_flags import set_enabled


def _apply_ai_research_patches(stack: ExitStack) -> None:
    stack.enter_context(
        patch(
            "sophie_bot.modules.ai.filters.ai_mode.resolve_chat_capabilities",
            AsyncMock(return_value=get_capabilities(AIMode.support)),
        )
    )
    stack.enter_context(
        patch("sophie_bot.modules.ai.filters.quota.check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    )
    stack.enter_context(patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)))


@pytest.mark.asyncio
async def test_research_command_is_silent_when_feature_flag_disabled(test_client: TestClient) -> None:
    await set_enabled("ai_research", False)
    group_chat = ChatFactory.create_group(chat_id=-1002910000001, title="Research Disabled Group")
    user_wrapper = test_client.create_user(user_id=929100001, first_name="ResearchUser", username="research_user")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    with ExitStack() as stack:
        _apply_ai_research_patches(stack)
        workflow_mock = stack.enter_context(
            patch("sophie_bot.modules.ai.handlers.research.run_research_workflow", AsyncMock())
        )
        requests = await test_client.send_command(
            command="research",
            args="telegram bot news",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert not requests
    workflow_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_research_command_returns_summary_and_sources(test_client: TestClient) -> None:
    await set_enabled("ai_research", True)
    group_chat = ChatFactory.create_group(chat_id=-1002910000002, title="Research Enabled Group")
    user_wrapper = test_client.create_user(user_id=929100002, first_name="ResearchUser", username="research_enabled")
    response = ResearchFinalResponse(
        research_title="Telegram bot research",
        text="Sophie can now research topics.",
        sources=[
            ResearchSource(
                title="Research source",
                url="https://example.com/research",
                snippet="Evidence snippet",
                published="2026-06-01",
            )
        ],
    )

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    with ExitStack() as stack:
        _apply_ai_research_patches(stack)
        workflow_mock = stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.research.run_research_workflow",
                AsyncMock(
                    return_value=ResearchWorkflowResult(
                        response=response,
                        model=SimpleNamespace(model_name="openai/gpt-5.5"),
                        message_history=[],
                    )
                ),
            )
        )
        requests = await test_client.send_command(
            command="research",
            args="telegram bot news",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /research when the feature is enabled"
    response_text = requests[-1].text or ""
    assert "Research" in response_text
    assert "Sophie can now research topics." in response_text
    assert "Research source" in response_text
    assert "https://example.com/research" in response_text
    workflow_mock.assert_awaited_once()

    await set_enabled("ai_research", False)
