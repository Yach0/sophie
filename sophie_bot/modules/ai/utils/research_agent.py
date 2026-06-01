from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from beanie import PydanticObjectId
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from stfu_tg import Template

from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_default_model
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.chatbot_agent import _get_search_tool
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate_schema_with_result
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.utils.ai_features import AI_FEATURE_RESEARCH
from sophie_bot.utils.i18n import gettext as _


class ResearchPlan(BaseModel):
    topic: str
    sub_questions: list[str]


class ResearchFindings(BaseModel):
    question: str
    results: list[dict[str, str]]


class ResearchAnalysis(BaseModel):
    key_insights: list[str]
    contradictions: list[str]
    gaps: list[str]


class ResearchReport(BaseModel):
    title: str
    summary: str
    sections: list[dict[str, str]]
    sources: list[dict[str, str]]


async def _charge_stage_usage(
    chat_iid: PydanticObjectId,
    model: Any,
    usage: Any | None,
) -> None:
    if usage and usage.total_tokens:
        await charge_ai_usage(chat_iid, AI_FEATURE_RESEARCH, model, usage)


def _build_history(system_prompt: str, prompt: str) -> NewAIMessageHistory:
    history = NewAIMessageHistory()
    history.add_system(system_prompt)
    history.prompt = [prompt]
    return history


def _normalize_search_result(result: object) -> dict[str, str]:
    if isinstance(result, Mapping):
        result_map = cast(Mapping[str, object], result)
        return {
            "title": str(result_map.get("title") or result_map.get("name") or ""),
            "url": str(result_map.get("url") or result_map.get("link") or ""),
            "snippet": str(
                result_map.get("snippet") or result_map.get("content") or result_map.get("description") or ""
            ),
        }

    return {
        "title": str(getattr(result, "title", "") or getattr(result, "name", "")),
        "url": str(getattr(result, "url", "") or getattr(result, "link", "")),
        "snippet": str(
            getattr(result, "snippet", "") or getattr(result, "content", "") or getattr(result, "description", "")
        ),
    }


async def _run_search_tool(
    question: str,
    chat_iid: PydanticObjectId,
    connection: ChatConnection,
    model: Any,
    search_tool: Any,
) -> list[dict[str, str]]:
    context = RunContext(
        deps=SophieAIToolContext(connection=connection, chat_tid=connection.tid, chat_iid=chat_iid),
        model=model,
        usage=RunUsage(),
        tool_name=search_tool.name,
    )
    if search_tool.name == "tavily_search":
        raw_results = await search_tool.function(query=question)
    elif search_tool.takes_ctx:
        raw_results = await search_tool.function(context, query=question, limit=5)
    else:
        raw_results = await search_tool.function(query=question, limit=5)
    return [_normalize_search_result(result) for result in cast(list[object], raw_results)]


async def run_research_workflow(
    topic: str,
    chat_tid: int,
    chat_iid: PydanticObjectId,
    connection: ChatConnection,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> ResearchReport:
    model = await get_chat_default_model(chat_iid, chat_tid=chat_tid)
    search_tool = await _get_search_tool(chat_tid)

    plan_result = await new_ai_generate_schema_with_result(
        _build_history(
            str(_("Break this topic into 3-5 focused sub-questions for research.")),
            Template(_("Research topic: {topic}"), topic=topic).to_html(),
        ),
        ResearchPlan,
        model=model,
        user_tracking_id=chat_iid,
    )
    await _charge_stage_usage(chat_iid, model, plan_result.usage)
    plan = plan_result.output

    if on_progress is not None:
        await on_progress(str(_("🔬 Researching...")))

    findings = [
        ResearchFindings(
            question=question,
            results=await _run_search_tool(question, chat_iid, connection, model, search_tool) if search_tool else [],
        )
        for question in plan.sub_questions[:5]
    ]

    if on_progress is not None:
        await on_progress(str(_("📊 Analyzing...")))

    findings_context = "\n\n".join(finding.model_dump_json() for finding in findings)
    analysis_result = await new_ai_generate_schema_with_result(
        _build_history(
            str(_("Analyze the research findings. Identify key insights, contradictions, and gaps.")),
            Template(_("Topic: {topic}\nFindings:\n{findings}"), topic=topic, findings=findings_context).to_html(),
        ),
        ResearchAnalysis,
        model=model,
        user_tracking_id=chat_iid,
    )
    await _charge_stage_usage(chat_iid, model, analysis_result.usage)
    analysis = analysis_result.output

    if on_progress is not None:
        await on_progress(str(_("📝 Writing report...")))

    report_result = await new_ai_generate_schema_with_result(
        _build_history(
            str(_("Write a structured research report from the analysis and findings.")),
            Template(
                _("Topic: {topic}\nAnalysis:\n{analysis}\nFindings:\n{findings}"),
                topic=topic,
                analysis=analysis.model_dump_json(),
                findings=findings_context,
            ).to_html(),
        ),
        ResearchReport,
        model=model,
        user_tracking_id=chat_iid,
    )
    await _charge_stage_usage(chat_iid, model, report_result.usage)
    return report_result.output
