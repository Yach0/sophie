from __future__ import annotations

import time
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Never, cast

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import (
    Agent,
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
)
from pydantic_ai.capabilities import Hooks, ModelFallback, WrapModelRequestHandler
from pydantic_ai.exceptions import RetryModelRequest, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, UserContent, UserPromptPart
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.models.fallback import FallbackOn
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
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
    AI_REQUEST_RETRY_ATTEMPTS,
    AIErrorContext,
    AIRetryCallback,
    ai_request_failed_from_error,
    capture_ai_error,
    is_provider_configuration_error,
    is_retryable_ai_provider_error,
)
from sophie_bot.modules.ai.utils.ai_model_factory import get_ai_model, registered_context_window_tokens
from sophie_bot.modules.ai.utils.ai_model_plan import (
    AIModelCandidate,
    AIModelPlan,
    request_has_images,
)
from sophie_bot.modules.ai.utils.ai_refusal import is_refusal_output

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
    return AIModelCandidate(
        model=model,
        model_name=AI_FALLBACK_MODEL_NAME,
        context_window_tokens=registered_context_window_tokens(AI_FALLBACK_MODEL_NAME),
    )


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
    return AIModelCandidate(
        model=agent_model,
        model_name=agent_model.model_name,
        context_window_tokens=registered_context_window_tokens(agent_model.model_name),
    )


def resolve_candidates(
    agent_model: Model,
    model_plan: AIModelPlan | None,
    has_images: bool,
) -> list[AIModelCandidate]:
    """The candidates to try for one request, best first.

    The agent's own model leads unless the plan deliberately ruled it out: a caller that built
    an agent around a model it chose by hand gets that model first, but a plan candidate skipped
    for lacking image support must not sneak back in through the agent.
    """
    agent_candidate = _agent_candidate(agent_model, model_plan)
    if model_plan is None:
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


def modern_context_candidates(model_plan: AIModelPlan) -> tuple[AIModelCandidate, ...]:
    """All candidates reachable by a text or image turn, including the last-resort model.

    Image filtering can expose a later candidate beyond the normal attempt cap. Admission
    must cover that chain too, before the current request's media has been assembled.
    """
    candidates: list[AIModelCandidate] = []
    for has_images in (False, True):
        for candidate in resolve_candidates(model_plan.primary, model_plan, has_images=has_images):
            candidate.require_context_window_tokens()
            if not any(
                existing.model is candidate.model and existing.context_window_tokens == candidate.context_window_tokens
                for existing in candidates
            ):
                candidates.append(candidate)
    return tuple(candidates)


def _should_try_next_model(error: BaseException) -> bool:
    """Whether another candidate is worth trying after this failure.

    Most provider failures earn a failover, not just the transient ones: the request that most
    needs a different model — an image sent to a model that cannot read one — comes back as a flat
    400 that retrying the same model would never fix. Two kinds do not. A usage limit is Sophie's
    own budget stopping the run, and the next model would only spend more of it. A configuration
    error is the provider refusing Sophie rather than the request, so every candidate is refused
    the same way and walking the chain only delays the error the user was always going to get.
    """
    return not isinstance(error, UsageLimitExceeded) and not is_provider_configuration_error(error)


@dataclass(frozen=True, slots=True)
class AIRequestOptions:
    user_tracking_id: str | int | PydanticObjectId | None = None
    session_id: str | None = None
    service_tier: str | None = None
    prompt_cache: bool = False
    context_projection: (
        Callable[
            [AIModelCandidate, Sequence[ModelRequest | ModelResponse]],
            tuple[list[UserContent], list[ModelRequest | ModelResponse]],
        ]
        | None
    ) = None

    @property
    def has_extra_body(self) -> bool:
        return self.user_tracking_id is not None or self.session_id is not None or self.service_tier is not None


class AIAgentResult[OutputT](BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    output: OutputT
    retries: int | None = None
    message_history: list[ModelRequest | ModelResponse]
    new_messages: list[ModelRequest | ModelResponse] = Field(default_factory=list)
    usage: RunUsage
    served_model: Model | None = None
    """The candidate that actually answered, which failover may have moved off the first one.

    Callers that report or bill the model should prefer this over the one they asked for, so a
    reply header and a usage charge both name the model the user's answer really came from.
    """


@dataclass(slots=True)
class _StreamChannel:
    """One indexed response buffer plus its debounced consumer."""

    callback: TextStreamCallback | None
    parts: dict[int, list[str]] = field(default_factory=dict)
    last_emit: float = 0.0
    last_emitted_text: str = ""

    @property
    def empty(self) -> bool:
        return not any(self.parts.values())

    def reset(self) -> None:
        self.parts.clear()
        self.last_emitted_text = ""

    def start(self, part_index: int, content: str) -> None:
        self.parts[part_index] = [content] if content else []

    def delta(self, part_index: int, content: str) -> None:
        self.parts.setdefault(part_index, []).append(content)

    def end(self, part_index: int, content: str) -> None:
        self.parts[part_index] = [content] if content else []

    def replace(self, content: str) -> None:
        self.parts = {0: [content]} if content else {}

    def render(self) -> str:
        return "".join("".join(self.parts[part_index]) for part_index in sorted(self.parts))

    async def emit(self, *, force: bool = False) -> None:
        if self.callback is None:
            return
        now = time.monotonic()
        if not force and now - self.last_emit < _STREAM_DEBOUNCE_SECONDS:
            return
        if self.empty:
            return
        rendered = self.render()
        if rendered == self.last_emitted_text:
            return
        self.last_emit = now
        self.last_emitted_text = rendered
        await self.callback(rendered)


@dataclass(frozen=True, slots=True)
class _StreamOutcome:
    output_text: str
    usage: RunUsage
    message_history: list[ModelRequest | ModelResponse]
    new_messages: list[ModelRequest | ModelResponse]
    first_token_seen: bool
    chunk_count: int


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


def _candidate_cache_settings(
    model_settings: dict[str, object] | None,
    request_options: AIRequestOptions | None,
    model: Model,
) -> dict[str, object] | None:
    """Enable only cache controls that the candidate's actual serializer supports.

    OpenRouter gates explicit boundaries through its downstream model profile. Native OpenAI
    caching is automatic; its opaque routing key improves prefix affinity. Compatible endpoints
    are not assumed to implement OpenAI's cache-key extension.
    """
    if request_options is None or not request_options.prompt_cache:
        return model_settings
    settings = dict(model_settings or {})
    if isinstance(model, OpenRouterModel):
        settings.update(
            openrouter_cache_instructions=True,
            openrouter_cache_messages=True,
            openrouter_cache_tool_definitions=True,
        )
    elif any(
        cls.__module__ == "pydantic_ai.models.anthropic" and cls.__name__ == "AnthropicModel"
        for cls in type(model).__mro__
    ):
        # Identify the actual native serializer without importing its optional SDK. This also
        # covers subclasses and Anthropic gateways whose provider name is not "anthropic".
        settings.update(
            anthropic_cache_instructions=True,
            anthropic_cache_messages=True,
            anthropic_cache_tool_definitions=True,
        )
        # Explicit boundaries also support gateways without automatic caching.
        settings["anthropic_cache"] = False
    elif (
        isinstance(model, (OpenAIChatModel, OpenAIResponsesModel))
        and model.client.base_url.host == "api.openai.com"
        and request_options.session_id is not None
    ):
        settings["openai_prompt_cache_key"] = request_options.session_id
    return settings or None


def _candidate_model_settings(
    model_settings: Mapping[str, object] | None,
    request_options: AIRequestOptions | None,
    candidate: AIModelCandidate,
    usage_limits: UsageLimits | None,
) -> dict[str, object]:
    candidate_options = _candidate_request_options(request_options, candidate)
    body_options = candidate_options
    if candidate_options is not None and candidate_options.prompt_cache:
        # Modern attribution uses opaque session keys, never real Telegram user/chat identifiers.
        # OpenRouter accepts session_id; native/compatible APIs must not receive that extension.
        body_options = replace(
            candidate_options,
            user_tracking_id=None,
            session_id=candidate_options.session_id if isinstance(candidate.model, OpenRouterModel) else None,
        )
    resolved_model_settings = _candidate_cache_settings(
        build_model_settings(model_settings, body_options), candidate_options, candidate.model
    )
    resolved_model_settings = _with_hard_output_token_limit(
        resolved_model_settings,
        candidate.model.settings,
        usage_limits,
    )
    settings = dict(resolved_model_settings or {})
    settings.setdefault("max_retries", AI_REQUEST_RETRY_ATTEMPTS - 1)
    return settings


def _with_hard_output_token_limit(
    model_settings: Mapping[str, object] | None,
    default_model_settings: Mapping[str, object] | None,
    usage_limits: UsageLimits | None,
) -> dict[str, object] | None:
    """Turn Sophie's output budget into the provider's own generation ceiling.

    Pydantic AI checks ``output_tokens_limit`` from provider-reported usage. Some providers only
    report that usage in their final stream event, so that check cannot stop a runaway response
    while it is being generated. ``max_tokens`` is the shared model setting Pydantic AI translates
    to each provider's native request field, making the provider end generation at the limit.

    A lower model- or call-specific ceiling remains authoritative. This preserves intentional
    provider settings while preventing any caller from raising the limit above Sophie's budget.
    """
    output_tokens_limit = usage_limits.output_tokens_limit if usage_limits is not None else None
    if output_tokens_limit is None:
        return dict(model_settings) if model_settings is not None else None

    configured_limits = [
        setting
        for setting in (
            (default_model_settings or {}).get("max_tokens"),
            (model_settings or {}).get("max_tokens"),
        )
        if isinstance(setting, int) and setting >= 0
    ]
    max_tokens = min([output_tokens_limit, *configured_limits])
    return {**(model_settings or {}), "max_tokens": max_tokens}


def _get_agent_model(agent: Agent[Any, Any]) -> Model:
    model = agent.model
    if model is None:
        raise ValueError("Agent model cannot be None for metrics tracking")
    if not isinstance(model, Model):
        raise TypeError(f"Agent model must be a Model instance, got {type(model)}")
    return model


@dataclass(slots=True)
class _ModelRequests:
    candidates: list[AIModelCandidate]
    agent_model: Model
    request_options: AIRequestOptions | None
    model_settings: Mapping[str, object] | None
    usage_limits: UsageLimits | None
    history_size: int
    allow_plan_failover: bool
    on_retry: AIRetryCallback | None
    operation_label: str
    active: AIModelCandidate
    attempt_started: float | None = None

    @classmethod
    def create(
        cls,
        agent: Agent[Any, Any],
        run_kwargs: Mapping[str, Any],
        request_options: AIRequestOptions | None,
        model_settings: Mapping[str, object] | None,
        model_plan: AIModelPlan | None,
        on_retry: AIRetryCallback | None,
        operation_label: str,
    ) -> _ModelRequests:
        agent_model = _get_agent_model(agent)
        candidates = resolve_candidates(
            agent_model,
            model_plan,
            request_has_images(run_kwargs.get("user_prompt"), run_kwargs.get("message_history")),
        )
        return cls(
            candidates=candidates,
            agent_model=agent_model,
            request_options=request_options,
            model_settings=model_settings,
            usage_limits=run_kwargs.get("usage_limits"),
            history_size=len(run_kwargs.get("message_history") or ()),
            allow_plan_failover=model_plan is not None,
            on_retry=on_retry,
            operation_label=operation_label,
            active=candidates[0],
        )

    def error_context(self) -> AIErrorContext:
        return AIErrorContext(
            operation=self.operation_label,
            model_name=self.active.model_name,
            primary_model_name=self.candidates[0].model_name if self.active is not self.candidates[0] else None,
        )

    def run_kwargs(self, run_kwargs: Mapping[str, Any]) -> dict[str, Any]:
        hooks = Hooks(
            prepare_model_request=self.prepare,
            after_model_request=self.accept_response,
            model_request=self.track_request,
        )
        capabilities = [hooks, *(run_kwargs.get("capabilities") or ())]
        if len(self.candidates) > 1:
            # Streamed responses cannot be rejected after they reach the caller.
            predicates: FallbackOn = self.falls_back
            if (
                self.allow_plan_failover
                and self.operation_label != "stream"
                and not run_kwargs.get("event_stream_handler")
            ):
                predicates = [self.falls_back, self.reject_response]
            capabilities.append(
                ModelFallback(
                    *(candidate.model for candidate in self.candidates[1:]), fallback_on=predicates, sticky=True
                )
            )
        kwargs = {**run_kwargs, "capabilities": capabilities}
        if self.candidates[0].model is not self.agent_model:
            kwargs["model"] = self.candidates[0].model
        return kwargs

    def prepare(self, _ctx: RunContext[object], request_context: ModelRequestContext) -> ModelRequestContext:
        self.active = next(candidate for candidate in self.candidates if candidate.model is request_context.model)
        messages = request_context.messages
        options = self.request_options
        if options is not None and options.context_projection is not None:
            current = messages[self.history_size :]
            try:
                prompt, history = options.context_projection(self.active, current[1:])
            except ValueError as error:
                index = self.candidates.index(self.active)
                if index + 1 < len(self.candidates):
                    raise RetryModelRequest(self.candidates[index + 1].model) from error
                raise ai_request_failed_from_error(error, self.error_context()) from error
            first_request = cast(ModelRequest, current[0])
            first_request = replace(
                first_request,
                parts=[
                    replace(part, content=prompt) if isinstance(part, UserPromptPart) else part
                    for part in first_request.parts
                ],
            )
            messages = [*history, first_request, *current[1:]]
        settings = _candidate_model_settings(
            {**(request_context.model_settings or {}), **(self.model_settings or {})},
            options,
            self.active,
            self.usage_limits,
        )
        self.attempt_started = time.perf_counter()
        return replace(request_context, messages=messages, model_settings=settings)

    async def falls_back(self, error: Exception) -> bool:
        self.finish_attempt(type(error).__name__)
        if not isinstance(error, AI_PROVIDER_EXCEPTIONS):
            return False
        allowed = _should_try_next_model(error) if self.allow_plan_failover else is_retryable_ai_provider_error(error)
        if allowed and self.active is not self.candidates[-1]:
            capture_ai_error(error, self.error_context(), level="warning")
            if self.on_retry is not None:
                await self.on_retry(self.candidates.index(self.active) + 1, len(self.candidates))
        return allowed

    def reject_response(self, response: ModelResponse) -> bool:
        rejected = (
            self.active is not self.candidates[-1] and not response.tool_calls and is_refusal_output(response.text)
        )
        if rejected:
            self.finish_attempt("ResponseRejected")
        return rejected

    def finish_attempt(self, error_type: str | None = None) -> None:
        if self.attempt_started is not None:
            track_ai_request(
                self.active.model, self.operation_label, time.perf_counter() - self.attempt_started, error_type
            )
            self.attempt_started = None

    def accept_response(
        self, _ctx: RunContext[object], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        self.finish_attempt()
        return response

    async def track_request(
        self, _ctx: RunContext[object], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        try:
            return await handler(request_context)
        except AI_PROVIDER_EXCEPTIONS as error:
            self.finish_attempt(type(error).__name__)
            raise
        finally:
            self.finish_attempt()

    def raise_failure(self, error: Exception) -> Never:
        if isinstance(error, UsageLimitExceeded) or is_provider_configuration_error(error):
            raise error
        # The native group retains all attempts; the user-facing event names the last serving model.
        raise ai_request_failed_from_error(error, self.error_context()) from error

    def track_usage(self, messages: Sequence[ModelRequest | ModelResponse]) -> None:
        for response in messages:
            if not isinstance(response, ModelResponse):
                continue
            for attempt in [*(response.failed_attempts or ()), response]:
                if attempt.usage is None:
                    continue
                model = next(
                    (
                        candidate.model
                        for candidate in self.candidates
                        if candidate.model.model_name == attempt.model_name
                    ),
                    self.active.model,
                )
                usage = RunUsage()
                usage.incr(attempt.usage)
                track_ai_usage(model, usage)


async def _run_with_metrics[DepsT, OutputT](
    agent: Agent[DepsT, OutputT],
    run_kwargs: Mapping[str, Any],
    request_options: AIRequestOptions | None = None,
    model_settings: Mapping[str, object] | None = None,
    on_retry: AIRetryCallback | None = None,
    model_plan: AIModelPlan | None = None,
    operation_label: str = "text",
) -> AIAgentResult[OutputT]:
    requests = _ModelRequests.create(
        agent, run_kwargs, request_options, model_settings, model_plan, on_retry, operation_label
    )
    try:
        result = await agent.run(**requests.run_kwargs(run_kwargs))
    except AI_PROVIDER_EXCEPTIONS as error:
        requests.raise_failure(error)

    served_model = requests.active.model
    message_history = result.all_messages()
    retries = count_retries_from_messages(message_history)

    if result.usage:
        requests.track_usage(result.new_messages())
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
        new_messages=result.new_messages(),
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
    on_before_tool_call: ToolCallCallback | None = None,
    on_retry: AIRetryCallback | None = None,
    model_plan: AIModelPlan | None = None,
) -> AIAgentResult[str]:
    run_kwargs = _build_agent_run_kwargs(user_prompt, message_history, deps, usage_limits)
    if on_before_tool_call is not None:

        async def event_stream_handler(
            _ctx: RunContext[DepsT],
            events: AsyncIterable[AgentStreamEvent],
        ) -> None:
            async for event in events:
                if isinstance(event, FunctionToolCallEvent):
                    await on_before_tool_call(event.part.tool_name)

        run_kwargs["event_stream_handler"] = event_stream_handler
    return await _run_with_metrics(
        agent,
        run_kwargs,
        request_options=request_options,
        model_settings=model_settings,
        on_retry=on_retry,
        operation_label="text",
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
    run_kwargs.update(extra_run_kwargs)
    return await _run_with_metrics(
        agent,
        run_kwargs,
        request_options=request_options,
        model_settings=model_settings,
        on_retry=on_retry,
        operation_label="structured",
        model_plan=model_plan,
    )


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
    requests: _ModelRequests,
    on_text_stream: TextStreamCallback,
    on_reasoning_stream: TextStreamCallback | None,
    on_before_tool_call: ToolCallCallback | None,
    on_tool_call: ToolCallCallback | None,
    seen_tool_names: set[str],
) -> _StreamOutcome:
    """Run the full agent loop while forwarding Pydantic AI stream events.

    ``Agent.run_stream`` treats the first text output as final and can skip later tool calls.
    ``Agent.run`` completes the graph and owns the final output, usage, and message history.
    """
    text = _StreamChannel(on_text_stream)
    reasoning = _StreamChannel(on_reasoning_stream)
    notify_tool_call = _tool_call_notifier(on_tool_call, seen_tool_names)
    stream_start = time.perf_counter()
    first_token_seen = False
    chunk_count = 0

    def note_first_token() -> None:
        nonlocal first_token_seen
        if first_token_seen or text.empty:
            return
        first_token_seen = True
        track_ai_time_to_first_token(requests.active.model, time.perf_counter() - stream_start)

    async def event_stream_handler(
        _ctx: RunContext[DepsT],
        events: AsyncIterable[AgentStreamEvent],
    ) -> None:
        nonlocal chunk_count
        text_response_started = False
        reasoning_response_started = False

        async for event in events:
            match event:
                case PartStartEvent(index=part_index, part=TextPart(content=content)):
                    if not text_response_started:
                        text.reset()
                        text_response_started = True
                    text.start(part_index, content)
                    note_first_token()
                    await text.emit()
                case PartStartEvent(index=part_index, part=ThinkingPart(content=content)):
                    if not reasoning_response_started:
                        reasoning.reset()
                        reasoning_response_started = True
                    reasoning.start(part_index, content)
                    await reasoning.emit()
                case PartDeltaEvent(index=part_index, delta=TextPartDelta(content_delta=content_delta)):
                    if not text_response_started:
                        text.reset()
                        text_response_started = True
                    chunk_count += 1
                    text.delta(part_index, content_delta or "")
                    note_first_token()
                    await text.emit()
                case PartDeltaEvent(index=part_index, delta=ThinkingPartDelta(content_delta=content_delta)):
                    if not reasoning_response_started:
                        reasoning.reset()
                        reasoning_response_started = True
                    reasoning.delta(part_index, content_delta or "")
                    await reasoning.emit()
                case PartEndEvent(index=part_index, part=TextPart(content=content)):
                    if not text_response_started:
                        text.reset()
                        text_response_started = True
                    text.end(part_index, content)
                    note_first_token()
                    await text.emit()
                case PartEndEvent(index=part_index, part=ThinkingPart(content=content)):
                    if not reasoning_response_started:
                        reasoning.reset()
                        reasoning_response_started = True
                    reasoning.end(part_index, content)
                    await reasoning.emit()
                case FunctionToolCallEvent():
                    await reasoning.emit(force=True)
                    await text.emit(force=True)
                    if on_before_tool_call is not None:
                        await on_before_tool_call(event.part.tool_name)
                    await notify_tool_call(event.part.tool_name)

    result = await agent.run(**run_kwargs, event_stream_handler=event_stream_handler)
    output_text = str(result.output)
    text.replace(output_text)
    await text.emit(force=True)
    return _StreamOutcome(
        output_text=output_text,
        usage=result.usage,
        message_history=result.all_messages(),
        new_messages=result.new_messages(),
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
    on_before_tool_call: ToolCallCallback | None = None,
    on_tool_call: ToolCallCallback | None = None,
    on_reasoning_stream: TextStreamCallback | None = None,
    on_retry: AIRetryCallback | None = None,
    model_plan: AIModelPlan | None = None,
    **extra_run_kwargs: Any,
) -> AIAgentResult[str]:
    run_kwargs = _build_agent_run_kwargs(user_prompt, message_history, deps, usage_limits)
    run_kwargs.update(extra_run_kwargs)
    requests = _ModelRequests.create(agent, run_kwargs, request_options, model_settings, model_plan, on_retry, "stream")
    try:
        outcome = await _stream_via_events(
            agent,
            requests.run_kwargs(run_kwargs),
            requests,
            on_text_stream,
            on_reasoning_stream,
            on_before_tool_call,
            on_tool_call,
            set(),
        )
    except AI_PROVIDER_EXCEPTIONS as error:
        requests.raise_failure(error)
    served_model = requests.active.model
    requests.track_usage(outcome.new_messages)
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
        new_messages=outcome.new_messages,
        usage=outcome.usage,
        served_model=served_model,
    )
