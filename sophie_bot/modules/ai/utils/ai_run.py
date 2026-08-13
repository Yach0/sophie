from __future__ import annotations

import time
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Final, TypeVar, cast

from pydantic import BaseModel, ConfigDict
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    AgentStreamEvent,
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RunContext,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    capture_run_messages,
)
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
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
from sophie_bot.modules.ai.utils.ai_model_factory import get_ai_model
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelPlan, request_has_images
from sophie_bot.modules.ai.utils.ai_refusal import AIModelRefused, is_refusal_output
from sophie_bot.utils.logger import log

ResponseT = TypeVar("ResponseT", bound=BaseModel)
TextStreamCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str], Awaitable[None]]

# Cheap, broadly-capable model used as a last resort once every candidate the catalog offers has
# failed. Better a degraded answer than none. It is appended to every plan, so a purpose the
# operator gave no failover chain still behaves exactly as it did before plans existed.
AI_FALLBACK_MODEL_NAME: Final = "mistralai/mistral-small-2603"

# Rendering the accumulated text costs a full join, so callbacks are debounced rather than fired on
# every delta. Matches the debounce the pydantic-ai `stream_text` helper applied before.
_STREAM_DEBOUNCE_SECONDS: Final = 0.2


def _resolve_fallback_model(primary_model: Model) -> Model | None:
    fallback_model = get_ai_model(AI_FALLBACK_MODEL_NAME)
    if fallback_model.model_name == primary_model.model_name:
        return None
    return fallback_model


def resolve_candidate_models(
    agent_model: Model,
    model_plan: AIModelPlan | None,
    has_images: bool,
) -> list[Model]:
    """The models to try for one request, best first.

    The agent's own model leads unless the plan deliberately ruled it out: a caller that built an
    agent around a model it chose by hand gets that model first, but a plan candidate skipped for
    lacking image support must not sneak back in through the agent. The cheap last-resort model
    closes every list, which is the pre-plan behaviour for purposes with a single model.
    """
    plan_models = list(model_plan.models(has_images=has_images)) if model_plan else []
    # Identity, not equality: two distinct model objects for the same provider settings compare
    # equal, and "is this the very object the agent holds" is the question actually being asked.
    ruled_out = (
        model_plan is not None
        and all(model is not agent_model for model in plan_models)
        and any(candidate.model is agent_model for candidate in model_plan.candidates)
    )

    candidates = (
        plan_models if ruled_out else [agent_model, *(model for model in plan_models if model is not agent_model)]
    )

    if (last_resort := _resolve_fallback_model(candidates[0] if candidates else agent_model)) is not None and all(
        last_resort is not candidate for candidate in candidates
    ):
        candidates.append(last_resort)
    return candidates


def _should_try_next_model(error: BaseException) -> bool:
    """Whether another candidate is worth trying after this failure.

    Every provider failure earns a failover, not just the transient ones: the request that most
    needs a different model — an image sent to a model that cannot read one — comes back as a flat
    400 that retrying the same model would never fix. A usage limit is the exception, because it is
    Sophie's own budget stopping the run and the next model would spend more of it.
    """
    return not isinstance(error, UsageLimitExceeded)


async def _run_with_model_candidates[FallbackOutputT](
    operation: Callable[[Model | None], Awaitable[FallbackOutputT]],
    candidates: Sequence[Model],
    agent_model: Model,
    is_refusal: Callable[[FallbackOutputT], bool] | None = None,
    on_retry: AIRetryCallback | None = None,
    operation_label: str = "agent",
) -> tuple[FallbackOutputT, Model]:
    """Run ``operation`` against each candidate in turn until one answers.

    A candidate is given up on when it fails in a way another model could survive, or when it
    finishes without producing a usable answer (see :func:`is_refusal_output`). The last candidate's
    refusal is returned rather than raised: an empty answer is still an answer, and turning the end
    of the chain into an error would change what the user sees for every mode at once.

    Each attempt is tracked under its own model name via :func:`track_ai_request`, and the model that
    actually served the request comes back with the result so callers attribute post-completion
    metrics (usage, agent/stream results) to it rather than to the first candidate.
    """
    last_error: BaseException | None = None

    for index, candidate in enumerate(candidates):
        # ``None`` leaves the agent on the model it was built with, so the common single-candidate
        # path issues exactly the request it always did, with no per-run model override.
        active_model = None if candidate is agent_model else candidate
        try:
            async with track_ai_request(candidate, operation_label):
                result = await run_ai_request_with_retries(partial(operation, active_model), on_retry=on_retry)
        except AI_PROVIDER_EXCEPTIONS as error:
            if not _should_try_next_model(error) or index == len(candidates) - 1:
                raise
            last_error = error
            log.warning(
                "AI request on %s failed (%s); trying %s",
                candidate.model_name,
                type(error).__name__,
                candidates[index + 1].model_name,
            )
            continue

        if is_refusal is not None and index < len(candidates) - 1 and is_refusal(result):
            last_error = AIModelRefused(candidate.model_name)
            log.warning(
                "AI request on %s produced no usable output; trying %s",
                candidate.model_name,
                candidates[index + 1].model_name,
            )
            continue

        return result, candidate

    raise last_error or RuntimeError("AI model candidate loop finished without returning or raising")


@dataclass(frozen=True, slots=True)
class AIRequestOptions:
    user_tracking_id: object | None = None
    session_id: str | None = None
    service_tier: str | None = None

    @property
    def has_extra_body(self) -> bool:
        return self.user_tracking_id is not None or self.session_id is not None or self.service_tier is not None


class AIAgentResult[OutputT](BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    output: OutputT
    steps: int | None = None
    retries: int | None = None
    message_history: list[ModelRequest | ModelResponse]
    usage: RunUsage
    truncated: bool = False
    """The run hit a usage limit and ``output`` is only what the model produced before that."""
    served_model: Model | None = None
    """The candidate that actually answered, which failover may have moved off the first one.

    Callers that report or bill the model should prefer this over the one they asked for, so a
    reply header and a usage charge both name the model the user's answer really came from.
    """


@dataclass(slots=True)
class _PartAccumulator:
    """Reassembles one kind of model output (text or thinking) from a run's event stream.

    Part indices restart with every model response, so segments are tracked through the part
    lifecycle instead of by index: ``end`` closes the in-flight segment with the authoritative
    content pydantic-ai hands back, and ``start`` closes any segment that never got an end event.

    Deltas go into a chunk list and closed segments into a running prefix, so rendering a growing
    reply on every delta stays linear instead of re-joining everything each time.
    """

    prefix: str = ""
    active: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.prefix and not any(self.active)

    def start(self, content: str) -> None:
        self._close()
        self.active = [content] if content else []

    def delta(self, content: str) -> None:
        self.active.append(content)

    def end(self, content: str) -> None:
        self.active = [content] if content else []
        self._close()

    def render(self) -> str:
        active = "".join(self.active)
        if not self.prefix:
            return active
        return f"{self.prefix}\n\n{active}" if active else self.prefix

    def _close(self) -> None:
        segment = "".join(self.active)
        self.active = []
        if not segment:
            return
        self.prefix = f"{self.prefix}\n\n{segment}" if self.prefix else segment


@dataclass(slots=True)
class _StreamChannel:
    """One accumulated output channel (answer text or reasoning) plus its debounced consumer."""

    callback: TextStreamCallback | None
    parts: _PartAccumulator = field(default_factory=_PartAccumulator)
    last_emit: float = 0.0

    async def emit(self) -> None:
        if self.callback is None:
            return
        now = time.monotonic()
        if now - self.last_emit < _STREAM_DEBOUNCE_SECONDS:
            return
        self.last_emit = now
        await self.callback(self.parts.render())


@dataclass(frozen=True, slots=True)
class ChatbotStreamOptions:
    """Chat-level switches for how a streamed chatbot run behaves.

    ``continuation`` off restores the pre-continuation `Agent.run_stream` path, which cannot report
    reasoning or partial output — the other two switches do nothing while it is off.
    """

    continuation: bool = True
    partial_on_limit: bool = False


@dataclass(frozen=True, slots=True)
class _StreamOutcome:
    output_text: str
    usage: RunUsage
    message_history: list[ModelRequest | ModelResponse]
    first_token_seen: bool
    chunk_count: int
    truncated: bool = False


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


def _get_agent_model(agent: Agent[Any, Any]) -> Model:
    model = agent.model
    if model is None:
        raise ValueError("Agent model cannot be None for metrics tracking")
    if not isinstance(model, Model):
        raise TypeError(f"Agent model must be a Model instance, got {type(model)}")
    return model


async def _run_with_retries_and_metrics[DepsT, OutputT](
    agent: Agent[DepsT, OutputT],
    run_kwargs: Mapping[str, Any],
    on_retry: AIRetryCallback | None = None,
    model_plan: AIModelPlan | None = None,
) -> AIAgentResult[OutputT]:
    model = _get_agent_model(agent)
    candidates = resolve_candidate_models(
        model,
        model_plan,
        request_has_images(run_kwargs.get("user_prompt"), run_kwargs.get("message_history")),
    )

    async def run_agent_once(active_model: Model | None) -> Any:
        if active_model is None:
            return await agent.run(**run_kwargs)
        run_with_fallback_kwargs = dict(run_kwargs)
        run_with_fallback_kwargs["model"] = active_model
        return await agent.run(**run_with_fallback_kwargs)

    try:
        result, served_model = await _run_with_model_candidates(
            run_agent_once,
            candidates,
            model,
            is_refusal=lambda run_result: is_refusal_output(run_result.output),
            on_retry=on_retry,
        )
    except AI_PROVIDER_EXCEPTIONS as error:
        raise ai_request_failed_from_error(error) from error

    message_history = cast(list[ModelRequest | ModelResponse], result.all_messages())
    retries = count_retries_from_messages(message_history)

    if result.usage:
        track_ai_usage(served_model, result.usage)
        track_ai_agent_result(
            served_model,
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
        served_model=served_model,
    )


def _build_agent_run_kwargs[DepsT](
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


async def run_ai_text[DepsT](
    agent: Agent[DepsT, str],
    user_prompt: str | Sequence[UserContent],
    message_history: list[ModelRequest | ModelResponse] | None = None,
    deps: DepsT | None = None,
    usage_limits: UsageLimits | None = None,
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_retry: AIRetryCallback | None = None,
    model_plan: AIModelPlan | None = None,
) -> AIAgentResult[str]:
    run_kwargs = _build_agent_run_kwargs(
        user_prompt, message_history, deps, usage_limits, request_options, model_settings
    )
    return await _run_with_retries_and_metrics(agent, run_kwargs, on_retry=on_retry, model_plan=model_plan)


async def run_ai_structured[DepsT, OutputT](
    agent: Agent[DepsT, OutputT],
    user_prompt: str | Sequence[UserContent],
    message_history: list[ModelRequest | ModelResponse] | None = None,
    deps: DepsT | None = None,
    usage_limits: UsageLimits | None = None,
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_retry: AIRetryCallback | None = None,
    model_plan: AIModelPlan | None = None,
    **extra_run_kwargs: Any,
) -> AIAgentResult[OutputT]:
    run_kwargs = _build_agent_run_kwargs(
        user_prompt, message_history, deps, usage_limits, request_options, model_settings
    )
    run_kwargs.update(extra_run_kwargs)
    return await _run_with_retries_and_metrics(agent, run_kwargs, on_retry=on_retry, model_plan=model_plan)


def _usage_from_messages(messages: Sequence[ModelRequest | ModelResponse]) -> RunUsage:
    """Rebuild run usage from captured messages, for a run that raised before reporting its own."""
    usage = RunUsage()
    for message in messages:
        if isinstance(message, ModelResponse):
            usage.requests += 1
            usage.incr(message.usage)
    return usage


def _tool_call_notifier(
    on_tool_call: ToolCallCallback | None,
    seen_tool_names: set[str],
) -> ToolCallCallback:
    """A tool-call callback that reports each tool name at most once per reply."""

    async def notify(tool_name: str) -> None:
        if on_tool_call is None or tool_name in seen_tool_names:
            return
        seen_tool_names.add(tool_name)
        await on_tool_call(tool_name)

    return notify


async def _stream_via_events[DepsT](
    agent: Agent[DepsT, str],
    run_kwargs: dict[str, Any],
    effective_model: Model,
    on_text_stream: TextStreamCallback,
    on_reasoning_stream: TextStreamCallback | None,
    on_tool_call: ToolCallCallback | None,
    seen_tool_names: set[str],
    partial_on_limit: bool,
) -> _StreamOutcome:
    """Stream a run over the agent's full event stream.

    ``Agent.run_stream`` ends the agent graph at the first text token, so a model that narrates
    before acting has its narration promoted to the final answer and its tool calls discarded.
    ``run_stream_events`` wraps ``Agent.run`` instead, so the tool-calling loop runs to completion.
    Text from every round is concatenated, because the run result only carries the last round's.
    """
    text = _StreamChannel(on_text_stream)
    reasoning = _StreamChannel(on_reasoning_stream)
    notify_tool_call = _tool_call_notifier(on_tool_call, seen_tool_names)
    stream_start = time.perf_counter()
    first_token_seen = False
    chunk_count = 0
    output_text = ""
    usage = RunUsage()
    result_message_history: list[ModelRequest | ModelResponse] = []

    def note_first_token() -> None:
        nonlocal first_token_seen
        if first_token_seen or text.parts.empty:
            return
        first_token_seen = True
        track_ai_time_to_first_token(effective_model, time.perf_counter() - stream_start)

    # `partial_on_limit` is the only consumer, and capturing retains a second reference to the whole
    # message list for the length of the run, so only pay for it when it can be read.
    capture = capture_run_messages() if partial_on_limit else nullcontext([])

    with capture as captured_messages:
        try:
            async with agent.run_stream_events(**run_kwargs) as events:
                async for event in events:
                    match event:
                        case PartStartEvent(part=TextPart(content=content)):
                            text.parts.start(content)
                            note_first_token()
                            await text.emit()
                        case PartStartEvent(part=ThinkingPart(content=content)):
                            reasoning.parts.start(content)
                            await reasoning.emit()
                        case PartDeltaEvent(delta=TextPartDelta(content_delta=content_delta)):
                            chunk_count += 1
                            text.parts.delta(content_delta or "")
                            note_first_token()
                            await text.emit()
                        case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=content_delta)):
                            reasoning.parts.delta(content_delta or "")
                            await reasoning.emit()
                        case PartEndEvent(part=TextPart(content=content)):
                            text.parts.end(content)
                            note_first_token()
                            await text.emit()
                        case PartEndEvent(part=ThinkingPart(content=content)):
                            reasoning.parts.end(content)
                            await reasoning.emit()
                        case FunctionToolCallEvent():
                            await notify_tool_call(event.part.tool_name)
                        case AgentRunResultEvent():
                            output_text = text.parts.render()
                            usage = event.result.usage
                            result_message_history = cast(
                                list[ModelRequest | ModelResponse], event.result.all_messages()
                            )
        except UsageLimitExceeded:
            if not partial_on_limit:
                raise
            captured = list(captured_messages)
            return _StreamOutcome(
                output_text=text.parts.render(),
                usage=_usage_from_messages(captured),
                message_history=captured,
                first_token_seen=first_token_seen,
                chunk_count=chunk_count,
                truncated=True,
            )

    if not result_message_history:
        raise UnexpectedModelBehavior("Agent event stream ended without a run result")

    return _StreamOutcome(
        output_text=output_text,
        usage=usage,
        message_history=result_message_history,
        first_token_seen=first_token_seen,
        chunk_count=chunk_count,
    )


async def _stream_via_run_stream[DepsT](
    agent: Agent[DepsT, str],
    run_kwargs: dict[str, Any],
    effective_model: Model,
    on_text_stream: TextStreamCallback,
    on_tool_call: ToolCallCallback | None,
    seen_tool_names: set[str],
) -> _StreamOutcome:
    """Rollback path for ``ai_chatbot_stream_continuation``: stream a single model response.

    This is the pre-continuation behaviour and carries its bug — the agent loop stops at the first
    text token, so tool calls made after a narration preamble never get a follow-up request.
    """
    stream_start = time.perf_counter()
    first_token_seen = False
    chunk_count = 0

    if on_tool_call is not None:
        notify_tool_call = _tool_call_notifier(on_tool_call, seen_tool_names)

        async def event_stream_handler(
            _ctx: RunContext[object],
            events: AsyncIterable[AgentStreamEvent],
        ) -> None:
            async for event in events:
                if isinstance(event, FunctionToolCallEvent):
                    await notify_tool_call(event.part.tool_name)

        run_kwargs["event_stream_handler"] = event_stream_handler

    async with agent.run_stream(**run_kwargs) as result_stream:
        accumulated_text = ""
        async for text_delta in result_stream.stream_text(delta=True, debounce_by=_STREAM_DEBOUNCE_SECONDS):
            if text_delta and not first_token_seen:
                first_token_seen = True
                track_ai_time_to_first_token(effective_model, time.perf_counter() - stream_start)
            chunk_count += 1
            accumulated_text += text_delta
            await on_text_stream(accumulated_text)

        return _StreamOutcome(
            output_text=await result_stream.get_output(),
            usage=result_stream.usage,
            message_history=cast(list[ModelRequest | ModelResponse], result_stream.all_messages()),
            first_token_seen=first_token_seen,
            chunk_count=chunk_count,
        )


async def run_ai_stream[DepsT](
    agent: Agent[DepsT, str],
    user_prompt: str | Sequence[UserContent],
    on_text_stream: TextStreamCallback,
    message_history: list[ModelRequest | ModelResponse] | None = None,
    deps: DepsT | None = None,
    usage_limits: UsageLimits | None = None,
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_tool_call: ToolCallCallback | None = None,
    on_reasoning_stream: TextStreamCallback | None = None,
    on_retry: AIRetryCallback | None = None,
    stream_options: ChatbotStreamOptions | None = None,
    model_plan: AIModelPlan | None = None,
    **extra_run_kwargs: Any,
) -> AIAgentResult[str]:
    options = stream_options or ChatbotStreamOptions()
    metrics_model = _get_agent_model(agent)
    candidates = resolve_candidate_models(metrics_model, model_plan, request_has_images(user_prompt, message_history))
    # Keep tool-thinking UI stable across stream retries.
    seen_tool_names: set[str] = set()

    async def run_stream_once(active_model: Model | None) -> _StreamOutcome:
        run_stream_kwargs = _build_agent_run_kwargs(
            user_prompt, message_history, deps, usage_limits, request_options, model_settings
        )
        run_stream_kwargs.update(extra_run_kwargs)
        if active_model is not None:
            run_stream_kwargs["model"] = active_model
        effective_model = active_model or metrics_model

        if not options.continuation:
            return await _stream_via_run_stream(
                agent,
                run_stream_kwargs,
                effective_model,
                on_text_stream,
                on_tool_call,
                seen_tool_names,
            )

        return await _stream_via_events(
            agent,
            run_stream_kwargs,
            effective_model,
            on_text_stream,
            on_reasoning_stream,
            on_tool_call,
            seen_tool_names,
            options.partial_on_limit,
        )

    try:
        outcome, served_model = await _run_with_model_candidates(
            run_stream_once,
            candidates,
            metrics_model,
            # A truncated run is never a refusal: it stopped on Sophie's own usage limit with text
            # already delivered, and re-running it on another model would spend the budget twice.
            is_refusal=lambda stream: not stream.truncated and is_refusal_output(stream.output_text),
            on_retry=on_retry,
        )
    except AI_PROVIDER_EXCEPTIONS as error:
        raise ai_request_failed_from_error(error) from error

    retries = count_retries_from_messages(outcome.message_history)
    track_ai_agent_result(
        served_model,
        outcome.usage,
        outcome.message_history,
        output_length=len(outcome.output_text),
        retries=retries,
    )
    track_ai_stream_result(
        served_model,
        chunks=outcome.chunk_count,
        text_length=len(outcome.output_text),
        first_token_seen=outcome.first_token_seen,
    )

    return AIAgentResult(
        output=outcome.output_text,
        retries=retries,
        message_history=outcome.message_history,
        usage=outcome.usage,
        truncated=outcome.truncated,
        served_model=served_model,
    )
