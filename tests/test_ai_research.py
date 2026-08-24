from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from pydantic_ai.messages import ModelResponse, ToolReturnPart
from pydantic_ai.models import Model
from stfu_tg import Title

from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.agent_tools.kagi_search import KagiSearchResult, kagi_search_tool
from sophie_bot.modules.ai.agent_tools.research import research_topic, research_topic_tool
from sophie_bot.modules.ai.agent_tools.tinyfish_search import (
    TinyFishSearchResult,
    search_tinyfish,
    tinyfish_search_tool,
)
from sophie_bot.modules.ai.json_schemas.research import (
    ResearchDecision,
    ResearchFinalResponse,
    ResearchQueryPlan,
    ResearchSearchQuery,
    ResearchSource,
)
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _build_fitting_reply_doc
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.chatbot_agent import (
    _get_search_tool,
    build_chatbot_usage_limits,
    get_chatbot_tools,
)
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
    search_web_for_research,
)
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import get_service_tier, get_value, is_enabled


@pytest.mark.asyncio
async def test_research_feature_flags_have_safe_defaults(db_init: object) -> None:
    assert await is_enabled("ai_chatbot_research_quote") is True
    assert await is_enabled("ai_research") is False
    # Empty by default now: research resolves from the catalog, the flag is only an override.
    assert await get_value("ai_research_model") == ""
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
        SimpleNamespace(output=final_response, usage=None, message_history=[], served_model=None),
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
        patch("sophie_bot.modules.ai.utils.research.resolve_chat_service_tier", AsyncMock(return_value=None)),
        patch(
            "sophie_bot.modules.ai.utils.research.get_chat_research_model_plan",
            AsyncMock(
                return_value=AIModelPlan(
                    candidates=(
                        AIModelCandidate(
                            model=cast(Model, SimpleNamespace(model_name="test-model")), model_name="test-model"
                        ),
                    )
                )
            ),
        ),
        patch(
            "sophie_bot.modules.ai.utils.research.run_research_structured_step",
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


async def test_the_reported_research_model_is_the_one_that_summarised() -> None:
    """Failover may move the summary off the plan's first candidate; the report must follow it."""
    connection = SimpleNamespace(tid=-100123, db_model=SimpleNamespace(iid="chat-iid"))
    settings = ResearchWorkflowSettings(max_rounds=1, queries_per_round=1, results_per_query=1, service_tier=None)
    source = ResearchSource(title="Only", url="https://example.com/only", snippet="Only snippet")
    final_response = ResearchFinalResponse(research_title="Answer", text="Answer", sources=[source])
    served_model = cast(Model, SimpleNamespace(model_name="backup-model"))

    generated_results = [
        SimpleNamespace(output=ResearchQueryPlan(queries=[ResearchSearchQuery(query="q", reason="r")]), usage=None),
        SimpleNamespace(output=final_response, usage=None, message_history=[], served_model=served_model),
    ]

    with (
        patch("sophie_bot.modules.ai.utils.research.get_research_settings", AsyncMock(return_value=settings)),
        patch("sophie_bot.modules.ai.utils.research.resolve_chat_service_tier", AsyncMock(return_value=None)),
        patch(
            "sophie_bot.modules.ai.utils.research.get_chat_research_model_plan",
            AsyncMock(
                return_value=AIModelPlan(
                    candidates=(
                        AIModelCandidate(
                            model=cast(Model, SimpleNamespace(model_name="primary-model")), model_name="primary-model"
                        ),
                    ),
                    failover=True,
                )
            ),
        ),
        patch(
            "sophie_bot.modules.ai.utils.research.run_research_structured_step",
            AsyncMock(side_effect=generated_results),
        ),
        patch(
            "sophie_bot.modules.ai.utils.research.search_web_for_research",
            AsyncMock(return_value=[source]),
        ),
    ):
        result = await run_research_workflow("Research this", connection)

    assert result.model is served_model
    assert result.response.research_model == "backup-model"


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
            SimpleNamespace(
                chat_tid=-100123,
                chat_iid="chat-iid",
                user_text=None,
                mode=AIMode.support,
                connection=SimpleNamespace(db_model=SimpleNamespace()),
            )
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
        tools = await get_chatbot_tools(-100123, get_capabilities(AIMode.support))

    assert research_topic_tool in tools

    with (
        patch("sophie_bot.modules.ai.utils.chatbot_agent.is_enabled", AsyncMock(return_value=False)),
        patch("sophie_bot.modules.ai.utils.chatbot_agent._get_search_tool", AsyncMock(return_value=None)),
    ):
        tools = await get_chatbot_tools(-100123, get_capabilities(AIMode.support))

    assert research_topic_tool not in tools


@pytest.mark.asyncio
async def test_build_chatbot_usage_limits_maps_token_limit() -> None:
    async def value_side_effect(feature: str, chat_tid: int | None = None) -> int:
        return {
            "ai_chatbot_request_limit": 3,
            "ai_chatbot_tool_calls_limit": 5,
            "ai_chatbot_response_tokens_limit": 2048,
        }[feature]

    with patch("sophie_bot.modules.ai.utils.chatbot_agent.get_value", AsyncMock(side_effect=value_side_effect)):
        limits = await build_chatbot_usage_limits(-100123)

    assert limits.request_limit == 3
    assert limits.tool_calls_limit == 5
    assert limits.output_tokens_limit == 2048


class _FakeTinyFishResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FailingTinyFishResponse:
    def raise_for_status(self) -> None:
        raise httpx2.HTTPError("503 Service Unavailable")

    def json(self) -> dict:
        raise AssertionError("response body must not be read after an HTTP error")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "tinyfish_key", "expected"),
    [
        ("tinyfish", "tf-key", tinyfish_search_tool),
        ("tinyfish", "", None),
    ],
)
async def test_get_search_tool_selects_tinyfish_by_flag(
    monkeypatch: pytest.MonkeyPatch, provider: str, tinyfish_key: str, expected: object
) -> None:
    monkeypatch.setattr(CONFIG, "tinyfish_api_key", tinyfish_key)
    with patch("sophie_bot.modules.ai.utils.chatbot_agent.get_value", AsyncMock(return_value=provider)):
        assert await _get_search_tool(-100123) is expected


@pytest.mark.asyncio
async def test_get_search_tool_keeps_existing_providers_working(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "tavily_api_key", "tvly-key")
    monkeypatch.setattr(CONFIG, "kagi_api_key", "kagi-key")
    with patch("sophie_bot.modules.ai.utils.chatbot_agent.get_value", AsyncMock(return_value="tavily")):
        assert await _get_search_tool(-100123) is not None
    with patch("sophie_bot.modules.ai.utils.chatbot_agent.get_value", AsyncMock(return_value="kagi")):
        assert await _get_search_tool(-100123) is kagi_search_tool


@pytest.mark.asyncio
async def test_search_web_for_research_maps_tinyfish_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "tinyfish_api_key", "tf-key")
    tiny_result = TinyFishSearchResult(
        title="Tiny title", url="https://example.com/tiny", snippet="Snip", published="2026-08-01"
    )
    with (
        patch("sophie_bot.modules.ai.utils.research.get_value", AsyncMock(return_value="tinyfish")),
        patch("sophie_bot.modules.ai.utils.research.search_tinyfish", AsyncMock(return_value=[tiny_result])) as mock,
    ):
        sources = await search_web_for_research(-100123, "query", 5)

    mock.assert_awaited_once_with("query", 5)
    assert sources == [
        ResearchSource(title="Tiny title", url="https://example.com/tiny", snippet="Snip", published="2026-08-01")
    ]


@pytest.mark.asyncio
async def test_search_web_for_research_requires_a_tinyfish_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "tinyfish_api_key", "")
    with (
        patch("sophie_bot.modules.ai.utils.research.get_value", AsyncMock(return_value="tinyfish")),
        pytest.raises(SophieException, match="Research requires a configured TinyFish API key"),
    ):
        await search_web_for_research(-100123, "query", 5)


@pytest.mark.asyncio
async def test_search_web_for_research_supports_only_kagi_and_tinyfish() -> None:
    with (
        patch("sophie_bot.modules.ai.utils.research.get_value", AsyncMock(return_value="tavily")),
        pytest.raises(SophieException, match="Set ai_search_provider to kagi or tinyfish"),
    ):
        await search_web_for_research(-100123, "query", 5)


@pytest.mark.asyncio
async def test_search_web_for_research_keeps_kagi_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "kagi_api_key", "kagi-key")
    kagi_result = KagiSearchResult(title="Kagi title", url="https://example.com/kagi", snippet="Snip", published=None)
    with (
        patch("sophie_bot.modules.ai.utils.research.get_value", AsyncMock(return_value="kagi")),
        patch("sophie_bot.modules.ai.utils.research.search_kagi", AsyncMock(return_value=[kagi_result])),
    ):
        sources = await search_web_for_research(-100123, "query", 5)

    assert sources == [
        ResearchSource(title="Kagi title", url="https://example.com/kagi", snippet="Snip", published=None)
    ]


@pytest.mark.asyncio
async def test_search_tinyfish_maps_fields_truncates_and_sends_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "tinyfish_api_key", "tf-key")
    captured: dict[str, object] = {}

    def fake_get(url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Any:
        captured.update({"url": url, "params": params, "headers": headers})
        return _FakeTinyFishResponse(
            {
                "results": [
                    {"title": "First", "url": "https://example.com/1", "snippet": "S", "date": "2026-08-01"},
                    {"title": "Second", "url": "https://example.com/2"},
                    {"title": "Third", "url": "https://example.com/3"},
                ]
            }
        )

    monkeypatch.setattr("sophie_bot.modules.ai.agent_tools.tinyfish_search.httpx2.get", fake_get)

    results = await search_tinyfish("telegram bot framework", 2)

    assert captured["url"] == "https://api.search.tinyfish.ai"
    assert captured["params"] == {"query": "telegram bot framework"}
    assert captured["headers"] == {"X-API-Key": "tf-key"}
    assert results == [
        TinyFishSearchResult(title="First", url="https://example.com/1", snippet="S", published="2026-08-01"),
        TinyFishSearchResult(title="Second", url="https://example.com/2", snippet=None, published=None),
    ]


@pytest.mark.asyncio
async def test_search_tinyfish_propagates_http_errors_for_retry_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CONFIG, "tinyfish_api_key", "tf-key")

    def failing_get(url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Any:
        return _FailingTinyFishResponse()

    monkeypatch.setattr("sophie_bot.modules.ai.agent_tools.tinyfish_search.httpx2.get", failing_get)

    with pytest.raises(httpx2.HTTPError):
        await search_tinyfish("query", 5)
