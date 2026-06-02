from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model

from sophie_bot.modules.ai.utils.ai_errors import AIRetryCallback
from sophie_bot.modules.ai.utils.ai_run import (
    AIAgentResult,
    AIRequestOptions,
    TextStreamCallback,
    ToolCallCallback,
    _build_run_kwargs,
    _pop_model_settings,
    _request_options_from_args,
    build_model_settings,
    run_ai_stream,
    run_ai_structured,
)

OutputT = TypeVar("OutputT")
ResponseT = TypeVar("ResponseT", bound=BaseModel)


async def new_ai_generate(
    history: Any,
    model: Model,
    agent_kwargs: Mapping[str, Any] | None = None,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    agent: Agent[Any, OutputT] | None = None,
    on_retry: AIRetryCallback | None = None,
    **kwargs: Any,
) -> AIAgentResult[OutputT]:
    resolved_request_options = _request_options_from_args(request_options, user_tracking_id, session_id, service_tier)
    agent_init_kwargs = dict(kwargs)
    base_model_settings = _pop_model_settings(agent_init_kwargs)
    active_agent = agent if agent is not None else cast(Agent[Any, OutputT], Agent(model, **agent_init_kwargs))
    return await run_ai_structured(
        active_agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        on_retry=on_retry,
        **_build_run_kwargs(agent_kwargs, resolved_request_options, base_model_settings),
    )


async def new_ai_generate_stream(
    history: Any,
    model: Model,
    on_text_stream: TextStreamCallback,
    agent_kwargs: Mapping[str, Any] | None = None,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    agent: Agent[Any, str] | None = None,
    on_tool_call: ToolCallCallback | None = None,
    on_retry: AIRetryCallback | None = None,
    **kwargs: Any,
) -> AIAgentResult[str]:
    resolved_request_options = _request_options_from_args(request_options, user_tracking_id, session_id, service_tier)
    agent_init_kwargs = dict(kwargs)
    base_model_settings = _pop_model_settings(agent_init_kwargs)
    active_agent = agent if agent is not None else Agent(model, **agent_init_kwargs)
    run_kwargs = _build_run_kwargs(agent_kwargs, resolved_request_options, base_model_settings)
    return await run_ai_stream(
        active_agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        on_text_stream=on_text_stream,
        model_settings=cast(Mapping[str, object] | None, run_kwargs.pop("model_settings", None)),
        on_tool_call=on_tool_call,
        on_retry=on_retry,
        **run_kwargs,
    )


async def new_ai_generate_schema(
    history: Any,
    schema: type[ResponseT],
    model: Model,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    on_retry: AIRetryCallback | None = None,
    **kwargs: Any,
) -> ResponseT:
    result = await new_ai_generate_schema_with_result(
        history,
        schema,
        model,
        user_tracking_id=user_tracking_id,
        session_id=session_id,
        service_tier=service_tier,
        request_options=request_options,
        on_retry=on_retry,
        **kwargs,
    )
    return result.output


async def new_ai_generate_schema_with_result(
    history: Any,
    schema: type[ResponseT],
    model: Model,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    on_retry: AIRetryCallback | None = None,
    **kwargs: Any,
) -> AIAgentResult[ResponseT]:
    resolved_request_options = _request_options_from_args(request_options, user_tracking_id, session_id, service_tier)
    agent_init_kwargs = dict(kwargs)
    base_model_settings = _pop_model_settings(agent_init_kwargs)
    agent = cast(Agent[Any, ResponseT], Agent(model, output_type=schema, **agent_init_kwargs))
    return await run_ai_structured(
        agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        on_retry=on_retry,
        **_build_run_kwargs(None, resolved_request_options, base_model_settings),
    )


__all__ = (
    "AIAgentResult",
    "AIRequestOptions",
    "TextStreamCallback",
    "ToolCallCallback",
    "build_model_settings",
    "new_ai_generate",
    "new_ai_generate_schema",
    "new_ai_generate_schema_with_result",
    "new_ai_generate_stream",
)
