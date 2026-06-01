from __future__ import annotations

import time
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from pydantic_ai import Agent, AgentStreamEvent, FunctionToolCallEvent, RunContext
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models import Model

from sophie_bot.metrics import (
    count_retries_from_messages,
    track_ai_agent_result,
    track_ai_request,
    track_ai_stream_result,
    track_ai_time_to_first_token,
)
from sophie_bot.modules.ai.utils.ai_agent_run import AIAgentResult, ai_agent_run
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.utils.exception import SophieException

OUTPUT_TYPE = TypeVar("OUTPUT_TYPE")
RESPONSE_TYPE = TypeVar("RESPONSE_TYPE", bound=BaseModel)
TextStreamCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AIRequestOptions:
    user_tracking_id: object | None = None
    session_id: str | None = None
    service_tier: str | None = None

    @property
    def has_extra_body(self) -> bool:
        return self.user_tracking_id is not None or self.session_id is not None or self.service_tier is not None


def build_model_settings(
    base_model_settings: Mapping[str, object] | None,
    request_options: AIRequestOptions | None,
) -> dict[str, object] | None:
    model_settings = dict(base_model_settings or {})
    if request_options is None or not request_options.has_extra_body:
        return model_settings or None

    extra_body = dict(cast(Mapping[str, object], model_settings.get("extra_body") or {}))
    if request_options.user_tracking_id is not None:
        extra_body["user"] = str(request_options.user_tracking_id)
    if request_options.session_id is not None:
        extra_body["session_id"] = request_options.session_id
    if request_options.service_tier is not None:
        extra_body["service_tier"] = request_options.service_tier
    model_settings["extra_body"] = extra_body
    return model_settings


def _request_options_from_args(
    request_options: AIRequestOptions | None,
    user_tracking_id: object | None,
    session_id: str | None,
    service_tier: str | None,
) -> AIRequestOptions:
    if request_options is not None:
        return request_options
    return AIRequestOptions(user_tracking_id=user_tracking_id, session_id=session_id, service_tier=service_tier)


def _build_run_kwargs(
    agent_kwargs: Mapping[str, Any] | None,
    request_options: AIRequestOptions,
    base_model_settings: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    run_kwargs = dict(agent_kwargs or {})
    run_model_settings_input = run_kwargs.pop("model_settings", None)
    merged_model_settings = dict(base_model_settings or {})
    if run_model_settings_input is not None:
        if not isinstance(run_model_settings_input, Mapping):
            raise TypeError("run model_settings must be a mapping when request options are injected")
        merged_model_settings.update(cast(Mapping[str, object], run_model_settings_input))

    run_model_settings = build_model_settings(merged_model_settings, request_options)
    if run_model_settings is not None:
        run_kwargs["model_settings"] = run_model_settings
    return run_kwargs


def _pop_model_settings(agent_init_kwargs: dict[str, Any]) -> Mapping[str, object] | None:
    model_settings = agent_init_kwargs.pop("model_settings", None)
    if model_settings is None:
        return None
    if isinstance(model_settings, Mapping):
        return cast(Mapping[str, object], model_settings)
    raise TypeError("model_settings must be a mapping when request options are injected")


def _resolve_model_for_metrics(agent: Agent[Any, Any], model: Model) -> Model:
    agent_model = agent.model
    if agent_model is None:
        return model
    if not isinstance(agent_model, Model):
        raise ValueError(f"Agent model must be a Model instance, got {type(agent_model)}")
    return agent_model


async def new_ai_generate(
    history: NewAIMessageHistory,
    model: Model,
    agent_kwargs: Mapping[str, Any] | None = None,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    agent: Agent[Any, OUTPUT_TYPE] | None = None,
    **kwargs: Any,
) -> AIAgentResult[OUTPUT_TYPE]:
    """
    Used to generate the AI Chat-bot result text.
    """
    resolved_request_options = _request_options_from_args(request_options, user_tracking_id, session_id, service_tier)
    agent_init_kwargs = dict(kwargs)
    base_model_settings = _pop_model_settings(agent_init_kwargs)
    active_agent = agent if agent is not None else cast(Agent[Any, OUTPUT_TYPE], Agent(model, **agent_init_kwargs))
    run_kwargs = _build_run_kwargs(agent_kwargs, resolved_request_options, base_model_settings)

    return await ai_agent_run(
        active_agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        **run_kwargs,
    )


async def new_ai_generate_stream(
    history: NewAIMessageHistory,
    model: Model,
    on_text_stream: TextStreamCallback,
    agent_kwargs: Mapping[str, Any] | None = None,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    agent: Agent[Any, str] | None = None,
    on_tool_call: ToolCallCallback | None = None,
    **kwargs: Any,
) -> AIAgentResult[str]:
    """
    Generate AI response while streaming cumulative text chunks through a callback.
    """
    resolved_request_options = _request_options_from_args(request_options, user_tracking_id, session_id, service_tier)
    agent_init_kwargs = dict(kwargs)
    base_model_settings = _pop_model_settings(agent_init_kwargs)
    active_agent = agent if agent is not None else Agent(model, **agent_init_kwargs)
    metrics_model = _resolve_model_for_metrics(active_agent, model)

    async with track_ai_request(metrics_model, "agent"):
        try:
            run_stream_kwargs = _build_run_kwargs(agent_kwargs, resolved_request_options, base_model_settings)
            stream_start = time.perf_counter()
            first_token_seen = False
            stream_chunk_count = 0
            if on_tool_call is not None:
                seen_tool_names: set[str] = set()

                async def event_stream_handler(
                    _ctx: RunContext[object],
                    events: AsyncIterable[AgentStreamEvent],
                ) -> None:
                    async for event in events:
                        if not isinstance(event, FunctionToolCallEvent):
                            continue

                        tool_name = event.part.tool_name
                        if tool_name in seen_tool_names:
                            continue

                        seen_tool_names.add(tool_name)
                        await on_tool_call(tool_name)

                run_stream_kwargs["event_stream_handler"] = event_stream_handler

            async with active_agent.run_stream(
                user_prompt=history.prompt,
                message_history=history.message_history,
                **run_stream_kwargs,
            ) as result_stream:
                accumulated_text = ""
                async for text_delta in result_stream.stream_text(delta=True, debounce_by=0.2):
                    if text_delta and not first_token_seen:
                        first_token_seen = True
                        track_ai_time_to_first_token(metrics_model, time.perf_counter() - stream_start)
                    stream_chunk_count += 1
                    accumulated_text += text_delta
                    await on_text_stream(accumulated_text)

                output_text = await result_stream.get_output()
                usage = result_stream.usage
                result_message_history = cast(list[ModelRequest | ModelResponse], result_stream.all_messages())
        except UnexpectedModelBehavior as error:
            raise SophieException("AI provider returned an invalid response. Please try again later.") from error
        except UsageLimitExceeded as error:
            raise SophieException(
                "AI request exceeded the configured usage limits. Please try a shorter request."
            ) from error
        except ModelHTTPError as error:
            raise SophieException(f"AI model error: {error.message}") from error

    retries = count_retries_from_messages(result_message_history)
    track_ai_agent_result(
        metrics_model,
        usage,
        result_message_history,
        output_length=len(output_text),
        retries=retries,
    )
    track_ai_stream_result(
        metrics_model,
        chunks=stream_chunk_count,
        text_length=len(output_text),
        first_token_seen=first_token_seen,
    )

    return AIAgentResult(
        output=output_text,
        retries=retries,
        message_history=result_message_history,
        usage=usage,
    )


async def new_ai_generate_schema(
    history: NewAIMessageHistory,
    schema: type[RESPONSE_TYPE],
    model: Model,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    **kwargs: Any,
) -> RESPONSE_TYPE:
    """
    Generate AI response with structured schema output.
    """
    resolved_request_options = _request_options_from_args(request_options, user_tracking_id, session_id, service_tier)
    agent_init_kwargs = dict(kwargs)
    base_model_settings = _pop_model_settings(agent_init_kwargs)

    agent = cast(Agent[Any, RESPONSE_TYPE], Agent(model, output_type=schema, **agent_init_kwargs))
    result: AIAgentResult[RESPONSE_TYPE] = await ai_agent_run(
        agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        **_build_run_kwargs(None, resolved_request_options, base_model_settings),
    )
    return result.output


async def new_ai_generate_schema_with_result(
    history: NewAIMessageHistory,
    schema: type[RESPONSE_TYPE],
    model: Model,
    user_tracking_id: object | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    request_options: AIRequestOptions | None = None,
    **kwargs: Any,
) -> AIAgentResult[RESPONSE_TYPE]:
    """
    Generate AI response with structured schema output and return full result including usage.
    """
    resolved_request_options = _request_options_from_args(request_options, user_tracking_id, session_id, service_tier)
    agent_init_kwargs = dict(kwargs)
    base_model_settings = _pop_model_settings(agent_init_kwargs)

    agent = cast(Agent[Any, RESPONSE_TYPE], Agent(model, output_type=schema, **agent_init_kwargs))
    return await ai_agent_run(
        agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        **_build_run_kwargs(None, resolved_request_options, base_model_settings),
    )
