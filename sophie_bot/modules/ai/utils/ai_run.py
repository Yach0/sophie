from __future__ import annotations

import time
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
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
    AIErrorContext,
    AIRetryCallback,
    ai_request_failed_from_error,
    capture_ai_error,
    is_provider_configuration_error,
    is_retryable_ai_provider_error,
    run_ai_request_with_retries,
)
from sophie_bot.modules.ai.utils.ai_model_factory import get_ai_model
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan, request_has_images
from sophie_bot.modules.ai.utils.ai_refusal import AIModelRefused, is_refusal_output
from sophie_bot.utils.logger import log

ResponseT = TypeVar("ResponseT", bound=BaseModel)
TextStreamCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str], Awaitable[None]]

# Cheap, broadly-capable model used as a last resort once every candidate the catalog offers has
# failed. Better a degraded answer than none. It closes every chain, so a purpose the operator gave
# no failover chain still behaves exactly as it did before plans existed.
AI_FALLBACK_MODEL_NAME: Final = "mistralai/mistral-small-2603"

# The most models one request may try, the last resort included. An operator may declare a longer
# chain than this and the panel still shows all of it, but a user waiting on provider after
# provider is worse served than by an earlier error, and a failure that is not model-specific costs
# one more upstream call per candidate before saying so.
AI_MAX_MODEL_ATTEMPTS: Final = 4

# Rendering the accumulated text costs a full join, so callbacks are debounced rather than fired on
# every delta. Matches the debounce the pydantic-ai `stream_text` helper applied before.
_STREAM_DEBOUNCE_SECONDS: Final = 0.2


def _last_resort_candidate(candidates: Sequence[AIModelCandidate]) -> AIModelCandidate | None:
    """The cheap candidate that closes a chain, unless that model is already in it.

    Membership is by model name, not by object identity: a catalog entry for the last-resort model
    carrying its own reasoning effort is a different object for the same upstream model, and trying
    it twice in a row only spends one more failed request.
    """
    model = get_ai_model(AI_FALLBACK_MODEL_NAME)
    if any(
        candidate.model_name == AI_FALLBACK_MODEL_NAME or candidate.model.model_name == model.model_name
        for candidate in candidates
    ):
        return None
    return AIModelCandidate(model=model, model_name=AI_FALLBACK_MODEL_NAME)


def _closed_chain(candidates: list[AIModelCandidate]) -> list[AIModelCandidate]:
    """A chain capped at :data:`AI_MAX_MODEL_ATTEMPTS`, closed by the last-resort model."""
    chain = candidates[: AI_MAX_MODEL_ATTEMPTS - 1]
    if (last_resort := _last_resort_candidate(chain)) is not None:
        chain.append(last_resort)
    return chain


def _agent_candidate(agent_model: Model, model_plan: AIModelPlan | None) -> AIModelCandidate:
    """The candidate for the model the agent was built with.

    A plan that already lists that model hands back its own entry, so the agent's model keeps the
    image support and the service tier its role declared rather than a bare assumption.
    """
    for candidate in model_plan.candidates if model_plan else ():
        if candidate.model is agent_model:
            return candidate
    return AIModelCandidate(model=agent_model, model_name=agent_model.model_name)


def resolve_candidates(
    agent_model: Model,
    model_plan: AIModelPlan | None,
    has_images: bool,
) -> list[AIModelCandidate]:
    """The candidates to try for one request, best first.

    With ``ai_model_failover`` off — or with no plan at all — the chain is the pre-plan one: the
    agent's own model, closed by the cheap last resort. With it on, the agent's own model still
    leads unless the plan deliberately ruled it out: a caller that built an agent around a model it
    chose by hand gets that model first, but a plan candidate skipped for lacking image support
    must not sneak back in through the agent.
    """
    agent_candidate = _agent_candidate(agent_model, model_plan)
    if model_plan is None or not model_plan.failover:
        return _closed_chain([agent_candidate])

    eligible = list(model_plan.eligible(has_images=has_images))
    # Identity, not equality: two distinct model objects for the same provider settings compare
    # equal, and "is this the very object the agent holds" is the question actually being asked.
    ruled_out = all(candidate.model is not agent_model for candidate in eligible) and any(
        candidate.model is agent_model for candidate in model_plan.candidates
    )
    if ruled_out:
        return _closed_chain(eligible)
    return _closed_chain([agent_candidate, *(c for c in eligible if c.model is not agent_model)])


def _should_try_next_model(error: BaseException) -> bool:
    """Whether another candidate is worth trying after this failure, with failover on.

    Most provider failures earn a failover, not just the transient ones: the request that most
    needs a different model — an image sent to a model that cannot read one — comes back as a flat
    400 that retrying the same model would never fix. Two kinds do not. A usage limit is Sophie's
    own budget stopping the run, and the next model would only spend more of it. A configuration
    error is the provider refusing Sophie rather than the request, so every candidate is refused
    the same way and walking the chain only delays the error the user was always going to get.
    """
    return not isinstance(error, UsageLimitExceeded) and not is_provider_configuration_error(error)


@dataclass(frozen=True, slots=True)
class CandidateChain:
    """One request's candidates plus the failover rules that go with them."""

    candidates: list[AIModelCandidate]
    should_try_next: Callable[[BaseException], bool]
    refusal_failover: bool


def build_candidate_chain(
    agent_model: Model,
    model_plan: AIModelPlan | None,
    has_images: bool,
) -> CandidateChain:
    """The candidates for one request and how far it may walk them.

    Flag off restores the pre-plan rules whole: one model plus the last resort, moved onto only by
    an error another attempt could actually survive, and never by an unusable answer.
    """
    failover = model_plan is not None and model_plan.failover
    return CandidateChain(
        candidates=resolve_candidates(agent_model, model_plan, has_images),
        should_try_next=_should_try_next_model if failover else is_retryable_ai_provider_error,
        refusal_failover=failover,
    )


async def _run_with_model_candidates[FallbackOutputT](
    operation: Callable[[AIModelCandidate], Awaitable[FallbackOutputT]],
    chain: CandidateChain,
    is_refusal: Callable[[FallbackOutputT], bool] | None = None,
    on_retry: AIRetryCallback | None = None,
    operation_label: str = "agent",
) -> tuple[FallbackOutputT, AIModelCandidate]:
    """Run ``operation`` against each candidate in turn until one answers.

    A candidate is given up on when it fails in a way another model could survive (see
    :meth:`CandidateChain.should_try_next`), or — with failover on — when it finishes without
    producing a usable answer, whether that is an empty output (:func:`is_refusal_output`) or an
    :exc:`AIModelRefused` a caller's own output validator raised. The last candidate's *returned*
    refusal is handed back rather than raised: an empty answer is still an answer, and turning the
    end of the chain into an error would change what the user sees for every mode at once. A
    *raised* refusal has no answer to hand back, so it reaches the caller that raised it.

    Each attempt is tracked under its own model name via :func:`track_ai_request`, and the candidate
    that actually served the request comes back with the result so callers attribute post-completion
    metrics (usage, agent/stream results) and charges to it rather than to the first candidate.

    This is also the single point where a provider failure becomes a reported, user-facing error:
    only here is it known which candidate was in play, so the failure that ends the chain is raised
    as :exc:`AIRequestFailed` carrying its Sentry event ID rather than escaping untagged.
    """
    last_error: BaseException | None = None
    candidates = chain.candidates
    lead_model_name = candidates[0].model_name if candidates else None

    for index, candidate in enumerate(candidates):
        is_last = index == len(candidates) - 1
        context = AIErrorContext(
            operation=operation_label,
            model_name=candidate.model_name,
            primary_model_name=lead_model_name if index else None,
        )
        try:
            async with track_ai_request(candidate.model, operation_label):
                result = await run_ai_request_with_retries(partial(operation, candidate), context, on_retry=on_retry)
        except AIModelRefused as refusal:
            if is_last or not chain.refusal_failover:
                raise
            last_error = refusal
            log.warning(
                "AI request on %s produced no usable output; trying %s",
                candidate.model_name,
                candidates[index + 1].model_name,
            )
            continue
        except AI_PROVIDER_EXCEPTIONS as error:
            if is_last or not chain.should_try_next(error):
                raise ai_request_failed_from_error(error, context) from error
            last_error = error
            log.warning(
                "AI request on %s failed (%s); trying %s",
                candidate.model_name,
                type(error).__name__,
                candidates[index + 1].model_name,
            )
            # A request the chain rescues still returns an answer, so nothing else would ever
            # surface a candidate that is failing every request in production. Warning level keeps
            # it apart from the failures a user actually saw.
            capture_ai_error(error, context, level="warning")
            continue

        if chain.refusal_failover and is_refusal is not None and not is_last and is_refusal(result):
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


def _merge_model_settings(
    base_model_settings: Mapping[str, object] | None,
    run_model_settings: object | None,
) -> Mapping[str, object] | None:
    """Model settings a caller passed through ``**extra_run_kwargs``, layered over the base ones."""
    if run_model_settings is None:
        return base_model_settings
    if not isinstance(run_model_settings, Mapping):
        raise TypeError("run model_settings must be a mapping when request options are injected")
    return {**(base_model_settings or {}), **cast(Mapping[str, object], run_model_settings)}


def _candidate_request_options(
    request_options: AIRequestOptions | None,
    candidate: AIModelCandidate,
) -> AIRequestOptions | None:
    """Request options carrying the service tier of the candidate about to be attempted.

    A role's tier belongs to the model that role names, so failover must not send the primary's
    tier upstream with a different candidate: the tier is part of the request body, and the
    provider serves and bills whatever it is sent.
    """
    base = request_options or AIRequestOptions()
    service_tier = candidate.resolve_service_tier(base.service_tier)
    if service_tier == base.service_tier:
        return request_options
    return replace(base, service_tier=service_tier)


def _candidate_run_kwargs(
    run_kwargs: Mapping[str, Any],
    model_settings: Mapping[str, object] | None,
    request_options: AIRequestOptions | None,
    candidate: AIModelCandidate,
    agent_model: Model,
) -> dict[str, Any]:
    """The run kwargs for one attempt, built per candidate because the service tier is one of them."""
    candidate_kwargs = dict(run_kwargs)
    resolved_model_settings = build_model_settings(
        model_settings, _candidate_request_options(request_options, candidate)
    )
    if resolved_model_settings is not None:
        candidate_kwargs["model_settings"] = resolved_model_settings
    # Leaving the model out keeps the agent on the one it was built with, so the common
    # single-candidate path issues exactly the request it always did, with no per-run override.
    if candidate.model is not agent_model:
        candidate_kwargs["model"] = candidate.model
    return candidate_kwargs


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
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_retry: AIRetryCallback | None = None,
    model_plan: AIModelPlan | None = None,
) -> AIAgentResult[OutputT]:
    agent_model = _get_agent_model(agent)
    chain = build_candidate_chain(
        agent_model,
        model_plan,
        request_has_images(run_kwargs.get("user_prompt"), run_kwargs.get("message_history")),
    )

    async def run_agent_once(candidate: AIModelCandidate) -> Any:
        return await agent.run(
            **_candidate_run_kwargs(run_kwargs, model_settings, request_options, candidate, agent_model)
        )

    result, served_candidate = await _run_with_model_candidates(
        run_agent_once,
        chain,
        is_refusal=lambda run_result: is_refusal_output(run_result.output),
        on_retry=on_retry,
    )

    served_model = served_candidate.model
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
) -> dict[str, Any]:
    """The kwargs shared by every attempt. Model settings are per candidate, so they are not here."""
    run_kwargs: dict[str, Any] = {"user_prompt": user_prompt}
    if message_history is not None:
        run_kwargs["message_history"] = message_history
    if deps is not None:
        run_kwargs["deps"] = deps
    if usage_limits is not None:
        run_kwargs["usage_limits"] = usage_limits
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
    run_kwargs = _build_agent_run_kwargs(user_prompt, message_history, deps, usage_limits)
    return await _run_with_retries_and_metrics(
        agent,
        run_kwargs,
        request_options=request_options,
        model_settings=model_settings,
        on_retry=on_retry,
        model_plan=model_plan,
    )


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
    run_kwargs = _build_agent_run_kwargs(user_prompt, message_history, deps, usage_limits)
    merged_model_settings = _merge_model_settings(model_settings, extra_run_kwargs.pop("model_settings", None))
    run_kwargs.update(extra_run_kwargs)
    return await _run_with_retries_and_metrics(
        agent,
        run_kwargs,
        request_options=request_options,
        model_settings=merged_model_settings,
        on_retry=on_retry,
        model_plan=model_plan,
    )


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
    agent_model = _get_agent_model(agent)
    chain = build_candidate_chain(agent_model, model_plan, request_has_images(user_prompt, message_history))
    base_run_kwargs = _build_agent_run_kwargs(user_prompt, message_history, deps, usage_limits)
    merged_model_settings = _merge_model_settings(model_settings, extra_run_kwargs.pop("model_settings", None))
    base_run_kwargs.update(extra_run_kwargs)
    # Keep tool-thinking UI stable across stream retries.
    seen_tool_names: set[str] = set()

    async def run_stream_once(candidate: AIModelCandidate) -> _StreamOutcome:
        run_stream_kwargs = _candidate_run_kwargs(
            base_run_kwargs, merged_model_settings, request_options, candidate, agent_model
        )
        effective_model = candidate.model

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

    outcome, served_candidate = await _run_with_model_candidates(
        run_stream_once,
        chain,
        # A truncated run is never a refusal: it stopped on Sophie's own usage limit with text
        # already delivered, and re-running it on another model would spend the budget twice.
        is_refusal=lambda stream: not stream.truncated and is_refusal_output(stream.output_text),
        on_retry=on_retry,
    )

    served_model = served_candidate.model
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
