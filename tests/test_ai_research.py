from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import ModelResponse, ToolReturnPart
from stfu_tg import Title

from sophie_bot.modules.ai.agent_tools.research import research_topic, research_topic_tool
from sophie_bot.modules.ai.json_schemas.research import (
    ResearchDecision,
    ResearchFinalResponse,
    ResearchQueryPlan,
    ResearchSearchQuery,
    ResearchSource,
)
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _build_fitting_reply_doc
from sophie_bot.modules.ai.utils.chatbot_agent import get_chatbot_tools
from sophie_bot.modules.ai.utils.chatbot_context import build_chatbot_instructions
from sophie_bot.modules.ai.utils.chatbot_response import TELEGRAM_MESSAGE_SAFE_LIMIT
from sophie_bot.modules.ai.utils.research import (
    ResearchProgressStage,
    ResearchWorkflowSettings,
    build_research_doc,
    build_research_markdown_file,
    render_research_markdown,
    research_markdown_filename,
    retrieve_latest_research_response,
    run_research_workflow,
)
from sophie_bot.utils.feature_flags import get_service_tier, get_value, is_enabled


@pytest.mark.asyncio
async def test_research_feature_flags_have_safe_defaults(db_init: object) -> None:
    assert await is_enabled("ai_chatbot_research_quote") is True
    assert await is_enabled("ai_research") is False
    assert await get_value("ai_research_model") == "openai/gpt-5.5"
    assert await get_value("ai_research_max_rounds") == 3
    assert await get_value("ai_research_queries_per_round") == 5
    assert await get_value("ai_research_results_per_query") == 5
    assert await get_service_tier("ai_research_service_tier") == "flex"


@pytest.mark.asyncio
async def test_run_research_workflow_runs_followup_searches() -> None:
    connection = SimpleNamespace(tid=-100123, db_model=SimpleNamespace(iid="chat-iid"))
    settings = ResearchWorkflowSettings(max_rounds=3, queries_per_round=2, results_per_query=2, service_tier=None)
    first_query = ResearchSearchQuery(query="initial query", reason="Start broad")
    followup_query = ResearchSearchQuery(query="followup query", reason="Fill gap")
    first_source = ResearchSource(title="First", url="https://example.com/first", snippet="First snippet")
    second_source = ResearchSource(title="Second", url="https://example.com/second", snippet="Second snippet")
    final_response = ResearchFinalResponse(
        research_title="Final answer",
        text="Final answer",
        sources=[first_source, second_source],
    )

    generated_results = [
        SimpleNamespace(output=ResearchQueryPlan(queries=[first_query]), usage=None),
        SimpleNamespace(
            output=ResearchDecision(action="search", followup_queries=[followup_query], reasoning="Need more"),
            usage=None,
        ),
        SimpleNamespace(
            output=ResearchDecision(action="continue", followup_queries=[], reasoning="Enough"),
            usage=None,
        ),
        SimpleNamespace(output=final_response, usage=None, message_history=[]),
    ]

    async def search_side_effect(chat_tid: int, query: str, limit: int) -> list[ResearchSource]:
        assert chat_tid == connection.tid
        assert limit == settings.results_per_query
        if query == first_query.query:
            return [first_source]
        if query == followup_query.query:
            return [second_source]
        return []

    progress_stages: list[ResearchProgressStage] = []

    async def record_progress(stage: ResearchProgressStage) -> None:
        progress_stages.append(stage)

    with (
        patch("sophie_bot.modules.ai.utils.research.get_research_settings", AsyncMock(return_value=settings)),
        patch(
            "sophie_bot.modules.ai.utils.research.get_research_model",
            AsyncMock(return_value=SimpleNamespace(model_name="test-model")),
        ),
        patch(
            "sophie_bot.modules.ai.utils.research.new_ai_generate_schema_with_result",
            AsyncMock(side_effect=generated_results),
        ) as generate_mock,
        patch(
            "sophie_bot.modules.ai.utils.research.search_web_for_research", AsyncMock(side_effect=search_side_effect)
        ),
    ):
        response = await run_research_workflow("Research this", connection, progress_callback=record_progress)

    assert response.response == final_response.model_copy(
        update={"research_query": "Research this", "research_model": response.model.model_name}
    )
    assert progress_stages == ["planning", "searching", "reviewing", "searching", "reviewing", "summarizing"]
    assert generate_mock.await_count == 4


def test_build_research_doc_formats_summary_and_sources() -> None:
    response = ResearchFinalResponse(
        research_title="Concise summary",
        text="**A concise summary**",
        sources=[
            ResearchSource(
                title="Source title",
                url="https://example.com/source",
                snippet="Supporting snippet",
                published="2026-01-01",
            )
        ],
    )

    html = build_research_doc(response).to_html()

    assert "Research" in html
    assert "<b>A concise summary</b>" in html
    assert "Source title" in html
    assert '<a href="https://example.com/source">' in html
    assert "&lt;a href" not in html
    assert "January 1, 2026" in html
    assert "Supporting snippet" in html


def test_retrieve_latest_research_response_reads_research_tool_return() -> None:
    source = ResearchSource(title="Source title", url="https://example.com/source", snippet="Supporting snippet")
    research_response = ResearchFinalResponse(
        research_title="Original research summary",
        text="Original research summary",
        sources=[source],
    )
    message_history = [ModelResponse(parts=[ToolReturnPart(tool_name="research_topic", content=research_response)])]

    assert retrieve_latest_research_response(message_history) == research_response


@pytest.mark.asyncio
async def test_research_tool_forwards_chatbot_progress_callback() -> None:
    expected_response = ResearchFinalResponse(research_title="Answer", text="Answer", sources=[])

    async def progress_callback(stage: ResearchProgressStage) -> None:
        assert stage == "planning"

    context = SimpleNamespace(
        deps=SimpleNamespace(
            connection=SimpleNamespace(),
            research_progress_callback=progress_callback,
        )
    )

    with patch(
        "sophie_bot.modules.ai.agent_tools.research.run_research_workflow_response",
        AsyncMock(return_value=expected_response),
    ) as workflow_mock:
        response = await research_topic(context, "complicated topic")

    assert response == expected_response
    workflow_mock.assert_awaited_once_with(
        "complicated topic",
        context.deps.connection,
        progress_callback=progress_callback,
    )


@pytest.mark.asyncio
async def test_build_fitting_reply_doc_fits_rendered_html_limit() -> None:
    with patch("sophie_bot.modules.ai.utils.chatbot_response.is_enabled", AsyncMock(return_value=True)):
        doc = await _build_fitting_reply_doc(
            Title("AI Chatbot"),
            "Long answer " * 600,
            model=None,
            result=SimpleNamespace(),
            explicit_debug_mode=False,
            chat_tid=-100123,
        )

    assert len(doc.to_html()) <= TELEGRAM_MESSAGE_SAFE_LIMIT


def test_render_research_markdown_has_metadata_and_sources_without_blockquote() -> None:
    response = ResearchFinalResponse(
        research_title="Research summary",
        text="Research summary",
        sources=[ResearchSource(title="Source title", url="https://example.com/source", snippet="Supporting snippet")],
        research_query="original question",
        research_model="test-model",
    )

    markdown_text = render_research_markdown(response)

    assert "Research summary" in markdown_text
    assert "original question" in markdown_text
    assert "test-model" in markdown_text
    assert "## Sources" in markdown_text
    assert "[Source title](https://example.com/source)" in markdown_text
    assert "Supporting snippet" in markdown_text
    assert ">" not in markdown_text


def test_research_markdown_file_uses_sanitized_title() -> None:
    response = ResearchFinalResponse(research_title="My research: title!", text="Research summary", sources=[])

    assert research_markdown_filename(response) == "My_research_title.md"
    assert build_research_markdown_file(response).filename == "My_research_title.md"


@pytest.mark.asyncio
async def test_chatbot_prompt_mentions_research_for_complicated_topics() -> None:
    async def enabled_side_effect(feature: str, chat_tid: int | None = None) -> bool:
        return feature == "ai_research"

    with (
        patch("sophie_bot.modules.ai.utils.chatbot_context.get_value", AsyncMock(return_value="Base system prompt")),
        patch("sophie_bot.modules.ai.utils.chatbot_context.is_enabled", AsyncMock(side_effect=enabled_side_effect)),
        patch("sophie_bot.modules.ai.utils.chatbot_context.AIMemoryModel.get_lines", AsyncMock(return_value=[])),
    ):
        instructions = await build_chatbot_instructions(
            SimpleNamespace(chat_tid=-100123, chat_iid="chat-iid", user_text=None)
        )

    assert "research tool to research complicated topics instead of plain web search" in instructions


@pytest.mark.asyncio
async def test_chatbot_tools_include_research_only_when_enabled() -> None:
    async def enabled_side_effect(feature: str, chat_tid: int | None = None) -> bool:
        return feature == "ai_research"

    with (
        patch("sophie_bot.modules.ai.utils.chatbot_agent.is_enabled", AsyncMock(side_effect=enabled_side_effect)),
        patch("sophie_bot.modules.ai.utils.chatbot_agent._get_search_tool", AsyncMock(return_value=None)),
    ):
        tools = await get_chatbot_tools(-100123)

    assert research_topic_tool in tools

    with (
        patch("sophie_bot.modules.ai.utils.chatbot_agent.is_enabled", AsyncMock(return_value=False)),
        patch("sophie_bot.modules.ai.utils.chatbot_agent._get_search_tool", AsyncMock(return_value=None)),
    ):
        tools = await get_chatbot_tools(-100123)

    assert research_topic_tool not in tools
