from __future__ import annotations

import time
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel
from pydantic_ai import Agent, AgentStreamEvent, FunctionToolCallEvent, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, UserContent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from sophie_bot.metrics import (
    count_retries_from_messages,
    track_ai_agent_result,
    track_ai_request,
    track_ai_stream_result,
    track_ai_time_to_first_token,
    track_ai_usage,
)
from sophie_bot.modules.ai.utils.ai_errors import (
    AI_PROVIDER_EXCEPTIONS,
    AIRetryCallback,
    ai_request_failed_from_error,
    run_ai_request_with_retries,
)

DepsT = TypeVar("DepsT")
OutputT = TypeVar("OutputT")
ResponseT = TypeVar("ResponseT", bound=BaseModel)
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


class AIAgentResult(BaseModel, Generic[OutputT]):
    output: OutputT
    steps: int | None = None
    retries: int | None = None
    message_history: list[ModelRequest | ModelResponse]
    usage: RunUsage


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


def _get_agent_model(agent: Agent[Any, Any]) -> Model:
    model = agent.model
    if model is None:
        raise ValueError("Agent model cannot be None for metrics tracking")
    if not isinstance(model, Model):
        raise ValueError(f"Agent model must be a Model instance, got {type(model)}")
    return model


async def _run_with_retries_and_metrics(
    agent: Agent[DepsT, OutputT],
    run_kwargs: Mapping[str, Any],
    on_retry: AIRetryCallback | None = None,
) -> AIAgentResult[OutputT]:
    model = _get_agent_model(agent)

    async def run_agent_once() -> Any:
        return await agent.run(**run_kwargs)

    try:
        async with track_ai_request(model, "agent"):
            result = await run_ai_request_with_retries(run_agent_once, on_retry=on_retry)
    except AI_PROVIDER_EXCEPTIONS as error:
        raise ai_request_failed_from_error(error) from error

    message_history = cast(list[ModelRequest | ModelResponse], result.all_messages())
    retries = count_retries_from_messages(message_history)

    if result.usage:
        track_ai_usage(model, result.usage)
        track_ai_agent_result(
            model,
            result.usage,
            message_history,
            output_length=len(str(result.output)),
            retries=retries,
        )

    return AIAgentResult(
        output=result.output,
        retries=retries,
        message_history=message_history,
        usage=result.usage,
    )


def _build_agent_run_kwargs(
    user_prompt: str | Sequence[UserContent],
    message_history: list[ModelRequest | ModelResponse] | None,
    deps: DepsT | None,
    usage_limits: UsageLimits | None,
    request_options: AIRequestOptions | None,
    model_settings: Mapping[str, object] | None,
) -> dict[str, Any]:
    run_kwargs: dict[str, Any] = {"user_prompt": user_prompt}
    if message_history is not None:
        run_kwargs["message_history"] = message_history
    if deps is not None:
        run_kwargs["deps"] = deps
    if usage_limits is not None:
        run_kwargs["usage_limits"] = usage_limits
    resolved_model_settings = build_model_settings(model_settings, request_options)
    if resolved_model_settings is not None:
        run_kwargs["model_settings"] = resolved_model_settings
    return run_kwargs


async def run_ai_text(
    agent: Agent[DepsT, str],
    user_prompt: str | Sequence[UserContent],
    message_history: list[ModelRequest | ModelResponse] | None = None,
    deps: DepsT | None = None,
    usage_limits: UsageLimits | None = None,
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_retry: AIRetryCallback | None = None,
) -> AIAgentResult[str]:
    run_kwargs = _build_agent_run_kwargs(
        user_prompt, message_history, deps, usage_limits, request_options, model_settings
    )
    return await _run_with_retries_and_metrics(agent, run_kwargs, on_retry=on_retry)


async def run_ai_structured(
    agent: Agent[DepsT, OutputT],
    user_prompt: str | Sequence[UserContent],
    message_history: list[ModelRequest | ModelResponse] | None = None,
    deps: DepsT | None = None,
    usage_limits: UsageLimits | None = None,
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_retry: AIRetryCallback | None = None,
    **extra_run_kwargs: Any,
) -> AIAgentResult[OutputT]:
    run_kwargs = _build_agent_run_kwargs(
        user_prompt, message_history, deps, usage_limits, request_options, model_settings
    )
    run_kwargs.update(extra_run_kwargs)
    return await _run_with_retries_and_metrics(agent, run_kwargs, on_retry=on_retry)


async def run_ai_stream(
    agent: Agent[DepsT, str],
    user_prompt: str | Sequence[UserContent],
    on_text_stream: TextStreamCallback,
    message_history: list[ModelRequest | ModelResponse] | None = None,
    deps: DepsT | None = None,
    usage_limits: UsageLimits | None = None,
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_tool_call: ToolCallCallback | None = None,
    on_retry: AIRetryCallback | None = None,
    **extra_run_kwargs: Any,
) -> AIAgentResult[str]:
    metrics_model = _get_agent_model(agent)
    # Keep tool-thinking UI stable across stream retries.
    seen_tool_names: set[str] = set()

    async def run_stream_once() -> tuple[str, RunUsage, list[ModelRequest | ModelResponse], bool, int]:
        run_stream_kwargs = _build_agent_run_kwargs(
            user_prompt, message_history, deps, usage_limits, request_options, model_settings
        )
        run_stream_kwargs.update(extra_run_kwargs)
        stream_start = time.perf_counter()
        first_token_seen = False
        stream_chunk_count = 0
        if on_tool_call is not None:

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

        async with agent.run_stream(**run_stream_kwargs) as result_stream:
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
            return output_text, usage, result_message_history, first_token_seen, stream_chunk_count

    try:
        async with track_ai_request(metrics_model, "agent"):
            (
                output_text,
                usage,
                result_message_history,
                first_token_seen,
                stream_chunk_count,
            ) = await run_ai_request_with_retries(run_stream_once, on_retry=on_retry)
    except AI_PROVIDER_EXCEPTIONS as error:
        raise ai_request_failed_from_error(error) from error

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
