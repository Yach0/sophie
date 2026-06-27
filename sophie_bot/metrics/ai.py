from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Literal

from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import MetricAttributes, change_gauge_metric, count_metric, distribution_metric
from sophie_bot.utils.logger import log

ProactiveAIEvent = Literal[
    "eligible_message",
    "quota_exhausted",
    "below_threshold",
    "lock_busy",
    "batch_started",
    "no_candidates",
    "decision_generated",
    "decision_skipped",
    "action_invalid_target",
    "action_answer_selected",
    "action_react_selected",
    "answer_sent",
    "answer_skipped",
    "reaction_sent",
    "reaction_skipped",
]
ProactiveAIAction = Literal["none", "answer", "react"]


def get_provider_from_model(model: Model) -> str:
    """Extract provider name from AI model"""
    # Extract provider name from the model's provider attribute
    if hasattr(model, "provider") and model.provider:
        provider_class_name = model.provider.__class__.__name__
        # Convert provider class names to simple provider names
        if "OpenAI" in provider_class_name:
            return "openai"
        if "Google" in provider_class_name:
            return "google"
        if "Mistral" in provider_class_name:
            return "mistral"

    # Fallback: try to infer from model name
    model_name = model.model_name.lower()
    if "gpt" in model_name or "openai" in model_name:
        return "openai"
    if "gemini" in model_name or "google" in model_name:
        return "google"
    if "mistral" in model_name or "codestral" in model_name or "pixtral" in model_name:
        return "mistral"

    return "unknown"


def get_model_name(model: Model) -> str:
    """Extract model name for metrics labeling"""
    return model.model_name


@asynccontextmanager
async def track_ai_request(model: Model, operation: str = "chat") -> AsyncGenerator[None, None]:
    """Context manager for tracking AI API requests"""
    if not CONFIG.metrics_enable:
        yield
        return

    provider = get_provider_from_model(model)
    model_name = get_model_name(model)
    start_time = time.perf_counter()

    count_metric(
        "sophie.ai.requests",
        attributes={"provider": provider, "model": model_name, "operation": operation},
    )

    error_type = "unknown"

    try:
        yield
    except Exception as e:
        error_type = type(e).__name__

        count_metric(
            "sophie.ai.errors",
            attributes={"provider": provider, "model": model_name, "error_type": error_type, "operation": operation},
        )

        log.debug(
            "AI request error tracked", provider=provider, model=model_name, operation=operation, error=error_type
        )
        raise
    finally:
        duration = time.perf_counter() - start_time
        distribution_metric(
            "sophie.ai.request.duration",
            duration,
            attributes={"provider": provider, "model": model_name, "operation": operation},
            unit="second",
        )


def _ai_metric_attributes(model: Model, operation: str) -> dict[str, str]:
    return {"provider": get_provider_from_model(model), "model": get_model_name(model), "operation": operation}


def count_retries_from_messages(message_history: list[ModelRequest | ModelResponse]) -> int:
    return sum(1 for message in message_history for part in message.parts if isinstance(part, RetryPromptPart))


def count_tool_calls_from_messages(message_history: list[ModelRequest | ModelResponse]) -> int:
    return sum(1 for message in message_history for part in message.parts if isinstance(part, ToolCallPart))


def track_ai_usage(model: Model, usage: RunUsage) -> None:
    """Track AI token usage metrics"""
    if not CONFIG.metrics_enable:
        return

    base_attributes = {"provider": get_provider_from_model(model), "model": get_model_name(model)}

    # Track different token types
    for value, token_type in (
        (usage.input_tokens, "request"),
        (usage.output_tokens, "response"),
        (usage.total_tokens, "total"),
    ):
        if value:
            count_metric(
                "sophie.ai.tokens",
                value,
                attributes={**base_attributes, "token_type": token_type},
            )


def track_ai_agent_result(
    model: Model,
    usage: RunUsage,
    message_history: list[ModelRequest | ModelResponse],
    *,
    operation: str = "agent",
    output_length: int | None = None,
    retries: int | None = None,
) -> None:
    """Track per-run AI agent telemetry that is only known after completion."""
    if not CONFIG.metrics_enable:
        return

    attributes = _ai_metric_attributes(model, operation)
    retry_count = retries if retries is not None else count_retries_from_messages(message_history)
    tool_call_count = usage.tool_calls or count_tool_calls_from_messages(message_history)

    distribution_metric("sophie.ai.agent.requests_per_run", usage.requests, attributes=attributes)
    distribution_metric("sophie.ai.agent.retries", retry_count, attributes=attributes)
    distribution_metric("sophie.ai.agent.tool_calls", tool_call_count, attributes=attributes)
    if output_length is not None:
        distribution_metric("sophie.ai.agent.output_length", output_length, attributes=attributes)

    if retry_count:
        count_metric("sophie.ai.agent.retried_runs", attributes={**attributes, "retry_count": retry_count})
    if tool_call_count:
        count_metric("sophie.ai.agent.tool_call_runs", attributes={**attributes, "tool_call_count": tool_call_count})


def track_ai_time_to_first_token(model: Model, seconds: float, *, operation: str = "agent") -> None:
    """Track streaming latency until the first text delta is received."""
    if not CONFIG.metrics_enable:
        return

    distribution_metric(
        "sophie.ai.agent.time_to_first_token",
        seconds,
        attributes=_ai_metric_attributes(model, operation),
        unit="second",
    )


def track_ai_stream_result(
    model: Model,
    *,
    operation: str = "agent",
    chunks: int,
    text_length: int,
    first_token_seen: bool,
) -> None:
    """Track aggregate telemetry for streamed agent responses."""
    if not CONFIG.metrics_enable:
        return

    attributes = _ai_metric_attributes(model, operation)
    distribution_metric("sophie.ai.agent.stream_chunks", chunks, attributes=attributes)
    distribution_metric("sophie.ai.agent.stream_output_length", text_length, attributes=attributes)
    count_metric("sophie.ai.agent.streams", attributes={**attributes, "first_token_seen": first_token_seen})


def track_ai_proactive_event(event: ProactiveAIEvent, attributes: MetricAttributes | None = None) -> None:
    """Track proactive AI reply lifecycle events."""
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.ai.proactive.events", attributes={"event": event, **dict(attributes or {})})


def track_ai_proactive_batch(message_count: int, action_count: int, attributes: MetricAttributes | None = None) -> None:
    """Track a proactive AI decision batch size and selected action count."""
    if not CONFIG.metrics_enable:
        return

    normalized_attributes = dict(attributes or {})
    distribution_metric(
        "sophie.ai.proactive.batch.messages",
        message_count,
        attributes=normalized_attributes,
    )
    distribution_metric(
        "sophie.ai.proactive.batch.actions",
        action_count,
        attributes=normalized_attributes,
    )


def track_ai_proactive_action(action: ProactiveAIAction, attributes: MetricAttributes | None = None) -> None:
    """Track proactive AI action decisions."""
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.ai.proactive.actions", attributes={"action": action, **dict(attributes or {})})


@asynccontextmanager
async def track_ai_tool(tool_name: str) -> AsyncGenerator[None, None]:
    """Context manager for tracking AI tool calls"""
    if not CONFIG.metrics_enable:
        yield
        return

    start_time = time.perf_counter()
    status = "success"

    try:
        yield
    except Exception as e:
        status = "error"
        log.debug("AI tool error tracked", tool=tool_name, error=type(e).__name__)
        raise
    finally:
        count_metric("sophie.ai.tool_calls", attributes={"tool_name": tool_name, "status": status})

        duration = time.perf_counter() - start_time
        distribution_metric("sophie.ai.tool.duration", duration, attributes={"tool_name": tool_name}, unit="second")


def track_ai_quota_exceeded(*, feature: str, chat_type: str = "unknown") -> None:
    if not CONFIG.metrics_enable:
        return

    count_metric("sophie.ai.quota.exceeded", attributes={"feature": feature, "chat_type": chat_type})


def track_active_conversation_start() -> None:
    """Track the start of an AI conversation"""
    if not CONFIG.metrics_enable:
        return

    change_gauge_metric("sophie.ai.active_conversations", 1)


def track_active_conversation_end() -> None:
    """Track the end of an AI conversation"""
    if not CONFIG.metrics_enable:
        return

    change_gauge_metric("sophie.ai.active_conversations", -1)


class AIConversationTracker:
    """Context manager for tracking active AI conversations"""

    def __init__(self) -> None:
        self.tracked = False

    async def __aenter__(self) -> AIConversationTracker:
        if CONFIG.metrics_enable:
            track_active_conversation_start()
            self.tracked = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        _ = (exc_type, exc_val, exc_tb)
        if self.tracked:
            track_active_conversation_end()


def track_ai_conversation():
    """Create an AI conversation tracker context manager"""
    return AIConversationTracker()
