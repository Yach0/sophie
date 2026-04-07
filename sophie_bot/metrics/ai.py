from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import change_gauge_metric, count_metric, distribution_metric
from sophie_bot.utils.logger import log


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


def track_ai_usage(model: Model, usage: RunUsage) -> None:
    """Track AI token usage metrics"""
    if not CONFIG.metrics_enable:
        return

    provider = get_provider_from_model(model)
    model_name = get_model_name(model)

    # Track different token types
    if usage.request_tokens:
        count_metric(
            "sophie.ai.tokens",
            usage.request_tokens,
            attributes={"provider": provider, "model": model_name, "token_type": "request"},
        )

    if usage.response_tokens:
        count_metric(
            "sophie.ai.tokens",
            usage.response_tokens,
            attributes={"provider": provider, "model": model_name, "token_type": "response"},
        )

    if usage.total_tokens:
        count_metric(
            "sophie.ai.tokens",
            usage.total_tokens,
            attributes={"provider": provider, "model": model_name, "token_type": "total"},
        )


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


# Decorator for AI operations
def instrument_ai_operation(operation: str = "chat"):
    """Decorator for AI operations that need metrics tracking"""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Try to extract model from arguments
            model = None
            for arg in args:
                if isinstance(arg, Model):
                    model = arg
                    break

            # Try to extract from kwargs
            if not model:
                model = kwargs.get("model")

            if model and CONFIG.metrics_enable:
                async with track_ai_request(model, operation):
                    result = await func(*args, **kwargs)

                    # If result has usage info, track it
                    if hasattr(result, "usage") and result.usage:
                        track_ai_usage(model, result.usage)

                    return result
            else:
                return await func(*args, **kwargs)

        return wrapper

    return decorator
