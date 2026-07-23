from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.common_tools.tavily import tavily_search_tool
from pydantic_ai.models import Model

from sophie_bot.config import CONFIG
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.agent_tools.deep_help import deep_help_tool
from sophie_bot.modules.ai.agent_tools.help import help_tool
from sophie_bot.modules.ai.agent_tools.kagi_search import kagi_search_tool
from sophie_bot.modules.ai.agent_tools.memory import forget_memory_tool, write_memory_tool
from sophie_bot.modules.ai.agent_tools.notes import get_note_content_tool, get_notes_tool
from sophie_bot.modules.ai.agent_tools.research import research_topic_tool
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import ModeCapabilities, get_capabilities
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions
from sophie_bot.modules.ai.utils.deep_help import is_deep_help_chat
from sophie_bot.modules.ai.utils.ai_tool_context import ResearchProgressCallback, SophieAIToolContext
from sophie_bot.modules.ai.utils.chatbot_context import build_chatbot_instructions
from sophie_bot.utils.feature_flags import get_value, is_enabled

CHATBOT_TOOLS: list[Any] = [
    write_memory_tool,
    forget_memory_tool,
    help_tool,
    get_notes_tool,
    get_note_content_tool,
]

_DEFAULT_CHATBOT_REQUEST_LIMIT = 4
_DEFAULT_CHATBOT_TOOL_CALLS_LIMIT = 6


@dataclass(frozen=True, slots=True)
class ChatbotRunConfig:
    agent: Agent[SophieAIToolContext, str]
    deps: SophieAIToolContext
    tools: Sequence[Any]
    usage_limits: UsageLimits
    request_options: AIRequestOptions


def build_chatbot_agent(model: Model, tools: list[Any]) -> Agent[SophieAIToolContext, str]:
    agent = Agent(
        model,
        deps_type=SophieAIToolContext,
        output_type=str,
        tools=tools,
    )

    @agent.instructions
    async def add_chatbot_instructions(ctx: RunContext[SophieAIToolContext]) -> str:
        return await build_chatbot_instructions(ctx.deps)

    return agent


async def _get_search_tool(chat_tid: int) -> Any | None:
    search_provider = str(await get_value("ai_search_provider", chat_tid=chat_tid)).lower()
    if search_provider == "tavily":
        return tavily_search_tool(api_key=CONFIG.tavily_api_key) if CONFIG.tavily_api_key else None
    return kagi_search_tool if CONFIG.kagi_api_key else None


_MEMORY_TOOL_NAMES = frozenset({"write_memory", "forget_memory"})
_NOTES_TOOL_NAMES = frozenset({"get_notes", "get_note_content"})


async def get_chatbot_tools(chat_tid: int, capabilities: ModeCapabilities) -> list[Any]:
    tools = [
        tool
        for tool in CHATBOT_TOOLS
        if (capabilities.memory or tool.name not in _MEMORY_TOOL_NAMES)
        and (capabilities.notes_read or tool.name not in _NOTES_TOOL_NAMES)
    ]
    if search_tool := await _get_search_tool(chat_tid):
        tools.append(search_tool)
    if await is_enabled("ai_research", chat_tid=chat_tid):
        tools.append(research_topic_tool)
    # The kill switch first: it is off by default, so the chat allowlist is usually never read.
    if await is_enabled("ai_deep_help", chat_tid=chat_tid) and (
        capabilities.deep_help or await is_deep_help_chat(chat_tid)
    ):
        tools.append(deep_help_tool)
    return tools


def _coerce_usage_limit(value: object, default: int | None = None) -> int | None:
    if value in {None, "", "none", "None", 0, "0"}:
        return default
    if isinstance(value, (int, float, str)):
        try:
            limit = int(value)
        except ValueError:
            return default
        return limit if limit > 0 else default
    return default


async def build_chatbot_usage_limits(chat_tid: int) -> UsageLimits:
    request_limit = _coerce_usage_limit(
        await get_value("ai_chatbot_request_limit", chat_tid=chat_tid), _DEFAULT_CHATBOT_REQUEST_LIMIT
    )
    tool_calls_limit = _coerce_usage_limit(
        await get_value("ai_chatbot_tool_calls_limit", chat_tid=chat_tid), _DEFAULT_CHATBOT_TOOL_CALLS_LIMIT
    )
    output_tokens_limit = _coerce_usage_limit(await get_value("ai_chatbot_response_tokens_limit", chat_tid=chat_tid))
    return UsageLimits(
        request_limit=request_limit,
        tool_calls_limit=tool_calls_limit,
        output_tokens_limit=output_tokens_limit,
    )


def _build_session_id(chat_iid: object, thread_id: int | None) -> str:
    if thread_id:
        return f"{chat_iid}:{thread_id}"
    return str(chat_iid)


async def build_chatbot_run_config(
    chat_tid: int,
    connection: ChatConnection,
    model: Model,
    user_text: str | None = None,
    user_tid: int | None = None,
    progress_callback: ResearchProgressCallback | None = None,
    thread_id: int | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    use_base_tools: bool = False,
    mode: AIMode = AIMode.support,
) -> ChatbotRunConfig:
    tools = CHATBOT_TOOLS if use_base_tools else await get_chatbot_tools(chat_tid, get_capabilities(mode))
    deps = SophieAIToolContext(
        connection=connection,
        chat_tid=chat_tid,
        chat_iid=connection.db_model.iid,
        mode=mode,
        user_text=user_text,
        research_progress_callback=progress_callback,
        user_tid=user_tid,
    )
    return ChatbotRunConfig(
        agent=build_chatbot_agent(model, tools),
        deps=deps,
        tools=tools,
        usage_limits=await build_chatbot_usage_limits(chat_tid),
        request_options=AIRequestOptions(
            user_tracking_id=connection.db_model.iid,
            session_id=session_id or _build_session_id(connection.db_model.iid, thread_id),
            service_tier=service_tier,
        ),
    )
