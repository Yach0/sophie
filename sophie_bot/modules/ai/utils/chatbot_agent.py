from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from beanie import PydanticObjectId
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.common_tools.tavily import tavily_search_tool
from pydantic_ai.models import Model
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.agent_tools.kagi_search import kagi_search_tool
from sophie_bot.modules.ai.agent_tools.memory import forget_memory_tool, write_memory_tool
from sophie_bot.modules.ai.agent_tools.notes import get_note_content_tool, get_notes_tool
from sophie_bot.modules.ai.agent_tools.research import research_topic_tool
from sophie_bot.modules.ai.agent_tools.sophie_help import sophie_help_tool
from sophie_bot.modules.ai.agent_tools.sophie_inspect import sophie_inspect_tool
from sophie_bot.modules.ai.agent_tools.tinyfish_search import tinyfish_search_tool
from sophie_bot.modules.ai.utils.ai_errors import AIRetryCallback
from sophie_bot.modules.ai.utils.ai_mode import ModeCapabilities, get_capabilities
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import (
    AIAgentResult,
    AIRequestOptions,
    ChatbotStreamOptions,
    TextStreamCallback,
    ToolCallCallback,
    run_ai_stream,
    run_ai_text,
)
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.chatbot_context import build_chatbot_instructions
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.sophie_inspect import is_sophie_inspect_chat
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.logger import log

CHATBOT_TOOLS: list[Any] = [
    write_memory_tool,
    forget_memory_tool,
    sophie_help_tool,
    get_notes_tool,
    get_note_content_tool,
]
_DEFAULT_CHATBOT_REQUEST_LIMIT = 4
_DEFAULT_CHATBOT_TOOL_CALLS_LIMIT = 6
_MEMORY_TOOL_NAMES = frozenset({"write_memory", "forget_memory"})
_NOTES_TOOL_NAMES = frozenset({"get_notes", "get_note_content"})


@dataclass(frozen=True, slots=True)
class ChatbotRunConfig:
    agent: Agent[SophieAIToolContext, str]
    usage_limits: UsageLimits
    request_options: AIRequestOptions


@dataclass(frozen=True, slots=True)
class ChatbotRunCallbacks:
    on_text_stream: TextStreamCallback | None = None
    on_tool_call: ToolCallCallback | None = None
    on_reasoning_stream: TextStreamCallback | None = None
    on_retry: AIRetryCallback | None = None


@dataclass(frozen=True, slots=True)
class ChatbotRunRequest:
    context: SophieAIToolContext
    history: AIMessageHistory
    model_plan: AIModelPlan
    service_tier: str | None = None
    thread_id: int | None = None
    session_id: str | None = None
    use_base_tools: bool = False
    stream_options: ChatbotStreamOptions | None = None
    callbacks: ChatbotRunCallbacks = field(default_factory=ChatbotRunCallbacks)
    charge_failure_policy: Literal["raise", "best_effort"] = "raise"


def build_chatbot_agent(model: Model, tools: list[Any]) -> Agent[SophieAIToolContext, str]:
    agent = Agent(model, deps_type=SophieAIToolContext, output_type=str, tools=tools)

    @agent.instructions
    async def add_chatbot_instructions(ctx: RunContext[SophieAIToolContext]) -> str:
        return await build_chatbot_instructions(ctx.deps)

    return agent


async def _get_search_tool(context: SophieAIToolContext) -> Any | None:
    search_provider = str(
        await get_value(
            "ai_search_provider",
            chat_tid=context.chat_tid,
            redis=context.services.redis,
        )
    ).lower()
    if search_provider == "tavily":
        return tavily_search_tool(api_key=CONFIG.tavily_api_key) if CONFIG.tavily_api_key else None
    if search_provider == "tinyfish":
        return tinyfish_search_tool if CONFIG.tinyfish_api_key else None
    return kagi_search_tool if CONFIG.kagi_api_key else None


async def get_chatbot_tools(
    context: SophieAIToolContext,
    capabilities: ModeCapabilities,
) -> list[Any]:
    tools = [
        tool
        for tool in CHATBOT_TOOLS
        if (capabilities.memory or tool.name not in _MEMORY_TOOL_NAMES)
        and (capabilities.notes_read or tool.name not in _NOTES_TOOL_NAMES)
    ]
    if search_tool := await _get_search_tool(context):
        tools.append(search_tool)
    if await is_enabled(
        "ai_research",
        chat_tid=context.chat_tid,
        redis=context.services.redis,
    ):
        tools.append(research_topic_tool)
    if await is_enabled(
        "ai_sophie_inspect",
        chat_tid=context.chat_tid,
        redis=context.services.redis,
    ) and (capabilities.sophie_inspect or await is_sophie_inspect_chat(context.chat_tid, redis=context.services.redis)):
        tools.append(sophie_inspect_tool)
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


async def build_chatbot_usage_limits(context: SophieAIToolContext) -> UsageLimits:
    redis = context.services.redis
    request_limit = _coerce_usage_limit(
        await get_value("ai_chatbot_request_limit", chat_tid=context.chat_tid, redis=redis),
        _DEFAULT_CHATBOT_REQUEST_LIMIT,
    )
    tool_calls_limit = _coerce_usage_limit(
        await get_value("ai_chatbot_tool_calls_limit", chat_tid=context.chat_tid, redis=redis),
        _DEFAULT_CHATBOT_TOOL_CALLS_LIMIT,
    )
    output_tokens_limit = _coerce_usage_limit(
        await get_value("ai_chatbot_response_tokens_limit", chat_tid=context.chat_tid, redis=redis)
    )
    return UsageLimits(
        request_limit=request_limit,
        tool_calls_limit=tool_calls_limit,
        output_tokens_limit=output_tokens_limit,
    )


def _build_session_id(chat_iid: PydanticObjectId, thread_id: int | None) -> str:
    return f"{chat_iid}:{thread_id}" if thread_id else str(chat_iid)


async def _build_chatbot_run_config(
    context: SophieAIToolContext,
    model: Model,
    *,
    thread_id: int | None,
    session_id: str | None,
    service_tier: str | None,
    use_base_tools: bool,
) -> ChatbotRunConfig:
    tools = CHATBOT_TOOLS if use_base_tools else await get_chatbot_tools(context, get_capabilities(context.mode))
    return ChatbotRunConfig(
        agent=build_chatbot_agent(model, tools),
        usage_limits=await build_chatbot_usage_limits(context),
        request_options=AIRequestOptions(
            user_tracking_id=context.chat_iid,
            session_id=session_id or _build_session_id(context.chat_iid, thread_id),
            service_tier=service_tier,
        ),
    )


async def run_chatbot(request: ChatbotRunRequest) -> AIAgentResult[str]:
    context = request.context
    model = request.model_plan.primary
    run_config = await _build_chatbot_run_config(
        context,
        model,
        thread_id=request.thread_id,
        session_id=request.session_id,
        service_tier=request.service_tier,
        use_base_tools=request.use_base_tools,
    )
    callbacks = request.callbacks
    if callbacks.on_text_stream is not None:
        result = await run_ai_stream(
            run_config.agent,
            user_prompt=request.history.prompt,
            message_history=request.history.message_history,
            on_text_stream=callbacks.on_text_stream,
            deps=context,
            usage_limits=run_config.usage_limits,
            request_options=run_config.request_options,
            on_before_tool_call=callbacks.on_tool_call,
            on_reasoning_stream=callbacks.on_reasoning_stream,
            on_retry=callbacks.on_retry,
            stream_options=request.stream_options,
            model_plan=request.model_plan,
        )
    else:
        result = await run_ai_text(
            run_config.agent,
            user_prompt=request.history.prompt,
            message_history=request.history.message_history,
            deps=context,
            usage_limits=run_config.usage_limits,
            request_options=run_config.request_options,
            on_retry=callbacks.on_retry,
            model_plan=request.model_plan,
        )

    if result.usage:
        served_model = result.served_model or model
        try:
            await charge_ai_usage(
                context.chat_iid,
                AI_FEATURE_CHATBOT,
                served_model,
                result.usage,
                redis=context.services.redis,
            )
        except (PyMongoError, RedisError) as error:
            if request.charge_failure_policy == "raise":
                raise
            log.warning("Failed to charge AI usage for chatbot", error=str(error))
    return result
