from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from random import choice
from typing import Final, Literal, TypeVar

from aiogram.types import BufferedInputFile
from babel.dates import format_date
from pydantic import BaseModel
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models import Model
from stfu_tg import (
    BlockQuote,
    Bold,
    Code,
    Doc,
    Italic,
    KeyValue,
    PreformattedHTML,
    Section,
    Template,
    Title,
    Url,
    VList,
)
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.agent_tools.kagi_search import KagiSearchResult, search_kagi
from sophie_bot.modules.ai.json_schemas.research import (
    ResearchDecision,
    ResearchFinalResponse,
    ResearchQueryPlan,
    ResearchSearchQuery,
    ResearchSource,
)
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_model_factory import get_research_model
from sophie_bot.modules.ai.utils.ai_tasks import AIStructuredTask, run_structured_task
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.utils.ai_features import AI_FEATURE_RESEARCH
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import get_service_tier, get_value
from sophie_bot.utils.i18n import gettext as _

_RESEARCH_SEARCH_PROVIDER_KAGI: Final[str] = "kagi"
_DEFAULT_MAX_ROUNDS: Final[int] = 3
_DEFAULT_QUERIES_PER_ROUND: Final[int] = 5
_DEFAULT_RESULTS_PER_QUERY: Final[int] = 5
_RESEARCH_MARKDOWN_FILENAME_FALLBACK: Final[str] = "research"
_RESEARCH_SOURCE_SNIPPET_LIMIT: Final[int] = 700

ResearchProgressStage = Literal["planning", "searching", "reviewing", "summarizing"]
ResearchProgressCallback = Callable[[ResearchProgressStage], Awaitable[None]]
ResearchStepT = TypeVar("ResearchStepT", bound=BaseModel)

_RESEARCH_PROGRESS_SUFFIXES: Final[dict[ResearchProgressStage, str]] = {
    "planning": "🧑‍🔬",
    "searching": "🔎",
    "reviewing": "🧐",
    "summarizing": "🧾",
}


def _research_progress_texts(stage: ResearchProgressStage) -> tuple[str, ...]:
    return {
        "planning": (
            _("Preparing search queries..."),
            _("Planning the research..."),
            _("Choosing what to search for..."),
        ),
        "searching": (
            _("Searching the internet..."),
            _("Looking it up online..."),
            _("Gathering sources from the web..."),
        ),
        "reviewing": (
            _("Reviewing search results..."),
            _("Checking if more searches are needed..."),
            _("Reading through the sources..."),
        ),
        "summarizing": (
            _("Summarizing the research..."),
            _("Putting the findings together..."),
            _("Preparing the final answer..."),
        ),
    }[stage]


def random_research_progress_text(stage: ResearchProgressStage) -> str:
    return choice(_research_progress_texts(stage))


def research_progress_suffix(stage: ResearchProgressStage) -> str:
    return _RESEARCH_PROGRESS_SUFFIXES[stage]


@dataclass(frozen=True)
class ResearchWorkflowSettings:
    max_rounds: int
    queries_per_round: int
    results_per_query: int
    service_tier: str | None


@dataclass(frozen=True)
class ResearchWorkflowResult:
    response: ResearchFinalResponse
    model: Model
    message_history: list[ModelRequest | ModelResponse]


def _coerce_positive_int(value: object, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed_value = int(value) if isinstance(value, int | float | str) else default
    except ValueError:
        return default
    if parsed_value <= 0:
        return default
    return min(parsed_value, maximum)


async def get_research_settings(chat_tid: int | None = None) -> ResearchWorkflowSettings:
    return ResearchWorkflowSettings(
        max_rounds=_coerce_positive_int(await get_value("ai_research_max_rounds", chat_tid=chat_tid), 3, 5),
        queries_per_round=_coerce_positive_int(
            await get_value("ai_research_queries_per_round", chat_tid=chat_tid), 5, 10
        ),
        results_per_query=_coerce_positive_int(
            await get_value("ai_research_results_per_query", chat_tid=chat_tid), 5, 10
        ),
        service_tier=await get_service_tier("ai_research_service_tier", chat_tid=chat_tid),
    )


def _limit_queries(queries: Iterable[ResearchSearchQuery], limit: int) -> list[ResearchSearchQuery]:
    limited_queries: list[ResearchSearchQuery] = []
    seen_queries: set[str] = set()
    for query in queries:
        normalized_query = query.query.strip()
        normalized_key = normalized_query.casefold()
        if not normalized_query or normalized_key in seen_queries:
            continue
        limited_queries.append(ResearchSearchQuery(query=normalized_query, reason=query.reason.strip()))
        seen_queries.add(normalized_key)
        if len(limited_queries) >= limit:
            break
    return limited_queries


def _source_from_kagi(result: KagiSearchResult) -> ResearchSource:
    return ResearchSource(
        title=result.title,
        url=result.url,
        snippet=result.snippet,
        published=result.published,
    )


async def search_web_for_research(chat_tid: int, query: str, limit: int) -> list[ResearchSource]:
    search_provider = str(await get_value("ai_search_provider", chat_tid=chat_tid)).lower()
    if search_provider != _RESEARCH_SEARCH_PROVIDER_KAGI:
        raise SophieException(
            _("Research currently supports the Kagi search provider. Set ai_search_provider to kagi to use it.")
        )
    if not CONFIG.kagi_api_key:
        raise SophieException(_("Research requires a configured Kagi API key."))

    results = await search_kagi(query, limit)
    return [_source_from_kagi(result) for result in results]


async def _run_queries(
    chat_tid: int, queries: list[ResearchSearchQuery], results_per_query: int
) -> list[ResearchSource]:
    sources: list[ResearchSource] = []
    seen_urls: set[str] = set()
    for query in queries:
        for source in await search_web_for_research(chat_tid, query.query, results_per_query):
            normalized_url = source.url.strip()
            if not normalized_url or normalized_url in seen_urls:
                continue
            sources.append(source)
            seen_urls.add(normalized_url)
    return sources


def _build_history(system_prompt: str, user_prompt: str) -> AIMessageHistory:
    history = AIMessageHistory()
    history.add_system(system_prompt)
    history.prompt = [user_prompt]
    return history


def _sources_payload(sources: list[ResearchSource]) -> str:
    return json.dumps([source.model_dump() for source in sources], ensure_ascii=False, indent=2)


def _queries_payload(queries: list[ResearchSearchQuery]) -> str:
    return json.dumps([query.model_dump() for query in queries], ensure_ascii=False, indent=2)


async def run_research_structured_step(
    history: AIMessageHistory,
    output_type: type[ResearchStepT],
    connection: ChatConnection,
    model: Model,
    settings: ResearchWorkflowSettings,
    session_suffix: str,
) -> AIAgentResult[ResearchStepT]:
    return await run_structured_task(
        AIStructuredTask(
            instructions="",
            output_type=output_type,
            feature=AI_FEATURE_RESEARCH,
        ),
        model,
        history,
        chat_iid=connection.db_model.iid,
        chat_tid=connection.tid,
        session_id=f"research:{connection.db_model.iid}:{session_suffix}",
        service_tier=settings.service_tier,
    )


async def _generate_initial_queries(
    prompt: str,
    connection: ChatConnection,
    model: Model,
    settings: ResearchWorkflowSettings,
) -> ResearchQueryPlan:
    history = _build_history(
        "\n".join(
            (
                "You plan web research for Sophie, a Telegram bot.",
                "Generate precise, diverse search queries that help answer the user's request.",
                "Do not answer the request yet. Return only the structured query plan.",
            )
        ),
        "\n".join(
            (
                "Research request:",
                prompt,
                f"Return up to {settings.queries_per_round} search queries.",
            )
        ),
    )
    result = await run_research_structured_step(
        history,
        ResearchQueryPlan,
        connection,
        model,
        settings,
        "queries",
    )
    return ResearchQueryPlan(queries=_limit_queries(result.output.queries, settings.queries_per_round))


async def _decide_next_step(
    prompt: str,
    queries: list[ResearchSearchQuery],
    sources: list[ResearchSource],
    round_index: int,
    connection: ChatConnection,
    model: Model,
    settings: ResearchWorkflowSettings,
) -> ResearchDecision:
    history = _build_history(
        "\n".join(
            (
                "You review web search results for a multistage research workflow.",
                "Choose action='search' only when follow-up searches are needed because evidence is missing, weak, outdated, or contradictory.",
                "Choose action='continue' when there is enough evidence to summarize.",
                "When action='search', return focused follow-up queries and do not exceed the requested limit.",
            )
        ),
        "\n".join(
            (
                "Research request:",
                prompt,
                f"Round: {round_index + 1} of {settings.max_rounds}",
                "Queries already run:",
                _queries_payload(queries),
                "Search results gathered so far:",
                _sources_payload(sources),
                f"Return up to {settings.queries_per_round} follow-up queries if more search is needed.",
            )
        ),
    )
    result = await run_research_structured_step(
        history,
        ResearchDecision,
        connection,
        model,
        settings,
        f"decision:{round_index}",
    )
    return ResearchDecision(
        action=result.output.action,
        followup_queries=_limit_queries(result.output.followup_queries, settings.queries_per_round),
        reasoning=result.output.reasoning,
    )


async def _summarize_research(
    prompt: str,
    sources: list[ResearchSource],
    connection: ChatConnection,
    model: Model,
    settings: ResearchWorkflowSettings,
) -> AIAgentResult[ResearchFinalResponse]:
    history = _build_history(
        "\n".join(
            (
                "You summarize multistage web research for Sophie, a Telegram bot.",
                "Use only the provided search results as evidence.",
                "Mention uncertainty when evidence is weak or sources disagree.",
                "Return a concise final answer, a short research_title, and a sources list containing only sources used to support the answer.",
            )
        ),
        "\n".join(
            (
                "Research request:",
                prompt,
                "Search results:",
                _sources_payload(sources),
            )
        ),
    )
    result = await run_research_structured_step(
        history,
        ResearchFinalResponse,
        connection,
        model,
        settings,
        "summary",
    )
    return result


async def run_research_workflow(
    prompt: str,
    connection: ChatConnection,
    progress_callback: ResearchProgressCallback | None = None,
) -> ResearchWorkflowResult:
    if not connection.db_model:
        raise SophieException(_("Research requires a saved chat context."))

    chat_tid = connection.tid
    settings = await get_research_settings(chat_tid)
    model = await get_research_model(chat_tid)
    if progress_callback is not None:
        await progress_callback("planning")
    query_plan = await _generate_initial_queries(prompt, connection, model, settings)
    current_queries = query_plan.queries
    all_sources: list[ResearchSource] = []
    seen_urls: set[str] = set()

    for round_index in range(settings.max_rounds):
        if not current_queries:
            break

        if progress_callback is not None:
            await progress_callback("searching")
        round_sources = await _run_queries(chat_tid, current_queries, settings.results_per_query)
        for source in round_sources:
            if source.url in seen_urls:
                continue
            all_sources.append(source)
            seen_urls.add(source.url)

        if round_index >= settings.max_rounds - 1:
            break

        if progress_callback is not None:
            await progress_callback("reviewing")
        decision = await _decide_next_step(
            prompt,
            current_queries,
            all_sources,
            round_index,
            connection,
            model,
            settings,
        )
        if decision.action == "continue":
            break
        current_queries = decision.followup_queries

    if not all_sources:
        return ResearchWorkflowResult(
            response=ResearchFinalResponse(
                research_title=_("Research"),
                text=_("I could not find enough search results to research this topic."),
                sources=[],
                research_query=prompt,
                research_model=model.model_name,
            ),
            model=model,
            message_history=[],
        )

    if progress_callback is not None:
        await progress_callback("summarizing")
    summary_result = await _summarize_research(prompt, all_sources, connection, model, settings)
    return ResearchWorkflowResult(
        response=summary_result.output.model_copy(
            update={
                "research_query": prompt,
                "research_model": model.model_name,
            }
        ),
        model=model,
        message_history=summary_result.message_history,
    )


async def run_research_workflow_response(
    prompt: str,
    connection: ChatConnection,
    progress_callback: ResearchProgressCallback | None = None,
) -> ResearchFinalResponse:
    result = await run_research_workflow(prompt, connection, progress_callback=progress_callback)
    return result.response


def _parse_source_date(value: str | None) -> date | None:
    if not value:
        return None

    normalized_value = value.strip()
    if not normalized_value:
        return None

    try:
        return datetime.fromisoformat(normalized_value.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    try:
        return parsedate_to_datetime(normalized_value).date()
    except (TypeError, ValueError):
        return None


def _research_response_from_tool_content(content: object) -> ResearchFinalResponse | None:
    if isinstance(content, ResearchFinalResponse):
        return content

    if isinstance(content, Mapping):
        try:
            return ResearchFinalResponse.model_validate(content)
        except ValueError:
            return None

    return None


def retrieve_latest_research_response(
    message_history: list[ModelRequest | ModelResponse],
) -> ResearchFinalResponse | None:
    for message in reversed(message_history):
        for part in reversed(message.parts):
            if not isinstance(part, ToolReturnPart) or part.tool_name != "research_topic":
                continue
            return _research_response_from_tool_content(part.content)
    return None


def format_research_source_title(source: ResearchSource, current_locale: str) -> Element:
    source_date = _parse_source_date(source.published)
    if source_date is None:
        return Url(source.title, source.url)

    formatted_date = format_date(source_date, format="long", locale=current_locale)
    return Url(Template("{title} {date}", title=Bold(source.title), date=Italic(formatted_date)), source.url)


def _truncate_with_ellipsis(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3].rstrip() + "..."


def build_research_source_section(
    source: ResearchSource,
    current_locale: str = "en_US",
    snippet_limit: int | None = None,
) -> Section:
    snippet = source.snippet or _("No snippet available.")
    if snippet_limit is not None:
        snippet = _truncate_with_ellipsis(snippet, snippet_limit)
    return Section(
        snippet,
        title=format_research_source_title(source, current_locale),
        title_underline=False,
        title_bold=False,
    )


def _build_research_sources_section(
    sources: list[ResearchSource],
    current_locale: str,
    snippet_limit: int | None = None,
) -> Section | None:
    if not sources:
        return None
    source_items = [
        build_research_source_section(source, current_locale=current_locale, snippet_limit=snippet_limit)
        for source in sources
    ]
    return Section(VList(*source_items), title=_("Sources"))


def build_research_doc(
    response: ResearchFinalResponse,
    header: Element | None = None,
    current_locale: str = "en_US",
) -> Doc:
    return Doc(
        header or Title(_("Research")),
        PreformattedHTML(ai_markdown_to_html(response.text, extract_headings=True)),
        BlockQuote(_build_research_sources_section(response.sources, current_locale), expandable=True)
        if response.sources
        else Section(Code(_("No sources."))),
    )


def build_research_file_doc(response: ResearchFinalResponse, current_locale: str = "en_US") -> Doc:
    return Doc(
        Section(
            KeyValue(_("Original request"), response.research_query or "-"),
            KeyValue(_("Research model"), response.research_model or "-"),
            response.text,
            _build_research_sources_section(
                response.sources,
                current_locale,
                snippet_limit=_RESEARCH_SOURCE_SNIPPET_LIMIT,
            ),
            title=response.research_title,
        )
    )


def render_research_markdown(response: ResearchFinalResponse, current_locale: str = "en_US") -> str:
    return build_research_file_doc(response, current_locale).to_md()


def research_markdown_filename(response: ResearchFinalResponse) -> str:
    normalized_title = re.sub(r"[^\w.-]+", "_", response.research_title, flags=re.ASCII).strip("._-")
    safe_title = normalized_title[:64] or _RESEARCH_MARKDOWN_FILENAME_FALLBACK
    return f"{safe_title}.md"


def build_research_markdown_file(response: ResearchFinalResponse, current_locale: str = "en_US") -> BufferedInputFile:
    markdown_text = render_research_markdown(response, current_locale)
    return BufferedInputFile(markdown_text.encode("utf-8"), filename=research_markdown_filename(response))
