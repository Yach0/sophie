from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory
from aiogram_test_framework.mock_bot import MockSession
from aiogram_test_framework.types import CapturedRequest, RequestType

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.json_schemas.research import ResearchFinalResponse, ResearchSource
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_header import AI_GENERATING_EMOJI_ID, AI_PROGRESS_LINE_EMOJI_IDS
from sophie_bot.modules.ai.utils.research import ResearchProgressCallback, ResearchWorkflowResult


def _apply_ai_research_patches(stack: ExitStack, test_client: TestClient) -> None:
    stack.enter_context(
        patch(
            "sophie_bot.modules.ai.middlewares.cache_user_messages.resolve_chat_mode",
            AsyncMock(return_value=AIMode.support),
        )
    )
    # The AI moderator runs whenever the chat's mode enables it, and would reach the network.
    stack.enter_context(
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_enabled", AsyncMock(return_value=False))
    )
    stack.enter_context(
        patch("sophie_bot.modules.ai.filters.quota.check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    )
    stack.enter_context(patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)))
    # The test Telegram transport has no built-in sendRichMessage response yet.
    session = cast(MockSession, test_client.bot.session)
    original_response = session._generate_response

    def respond_to_rich(bot: Bot, method_name: str, params: dict[str, Any]) -> Any:
        return original_response(
            bot=bot,
            method_name="sendMessage" if method_name == "sendRichMessage" else method_name,
            params=params,
        )

    stack.enter_context(patch.object(session, "_generate_response", side_effect=respond_to_rich))


def _rich_html(request: CapturedRequest) -> str:
    return request.params.get("rich_message", {}).get("html", "")


@pytest.mark.asyncio
async def test_research_command_returns_summary_and_sources(test_client: TestClient) -> None:
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

    result = ResearchWorkflowResult(
        response=response,
        model=SimpleNamespace(model_name="openai/gpt-5.5"),
        message_history=[],
    )

    async def finish_research(
        _prompt: str, _connection: object, *, progress_callback: ResearchProgressCallback, services: object
    ) -> ResearchWorkflowResult:
        for stage in ("planning", "searching", "reviewing", "summarizing"):
            await progress_callback(stage)
        return result

    with ExitStack() as stack:
        _apply_ai_research_patches(stack, test_client)
        stack.enter_context(patch("sophie_bot.modules.ai.utils.research.choice", side_effect=lambda texts: texts[0]))
        workflow_mock = stack.enter_context(
            patch("sophie_bot.modules.ai.handlers.research.run_research_workflow", AsyncMock(side_effect=finish_research))
        )
        requests = await test_client.send_command(
            command="research",
            args="telegram bot news",
            from_user=user_wrapper.user,
            chat=group_chat,
        )
    progress = [request for request in requests if request.request_type == RequestType.OTHER and _rich_html(request)]
    edits = [request for request in requests if request.request_type == RequestType.EDIT_MESSAGE_TEXT]
    assert len(progress) == 1
    assert len(edits) == 5
    assert all(request.params.get("rich_message") for request in [*progress, *edits])
    stage_texts = (
        "Starting the research...",
        "Preparing search queries...",
        "Searching the internet...",
        "Reviewing search results...",
        "Summarizing the research...",
    )
    for request, stage_text in zip([*progress, *edits[:-1]], stage_texts, strict=True):
        html = _rich_html(request)
        assert stage_text in html
        assert AI_GENERATING_EMOJI_ID in html
        assert all(html.count(emoji_id) == 1 for emoji_id in AI_PROGRESS_LINE_EMOJI_IDS)
        assert not any(suffix in html for suffix in ("🧑‍🔬", "🔎", "🧐", "🧾"))

    response_text = _rich_html(edits[-1])
    assert "Research" in response_text
    assert "Sophie can now research topics." in response_text
    assert "Research source" in response_text
    assert "https://example.com/research" in response_text
    assert all(emoji_id not in response_text for emoji_id in AI_PROGRESS_LINE_EMOJI_IDS)
    workflow_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_research_provider_failure_replies_after_rich_progress(test_client: TestClient) -> None:
    group_chat = ChatFactory.create_group(chat_id=-1002910000003, title="Research Failure Group")
    user_wrapper = test_client.create_user(user_id=929100003, first_name="ResearchUser", username="research_failure")
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    with ExitStack() as stack:
        _apply_ai_research_patches(stack, test_client)
        workflow_mock = stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.research.run_research_workflow",
                AsyncMock(side_effect=AIRequestFailed("known-reference")),
            )
        )
        requests = await test_client.send_command(
            command="research",
            args="telegram bot news",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    sends = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    progress = [request for request in requests if request.request_type == RequestType.OTHER and _rich_html(request)]
    assert len(sends) == 1
    assert len(progress) == 1
    assert "Starting the research..." in _rich_html(progress[0])
    assert all(emoji_id in _rich_html(progress[0]) for emoji_id in AI_PROGRESS_LINE_EMOJI_IDS)
    assert "Could not complete research" in (sends[0].text or "")
    assert not _rich_html(sends[0])
    assert not any(request.request_type == RequestType.EDIT_MESSAGE_TEXT for request in requests)
    workflow_mock.assert_awaited_once()
