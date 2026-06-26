from __future__ import annotations

from sophie_bot.metrics.ai import (
    count_retries_from_messages,
    track_ai_agent_result,
    track_ai_conversation,
    track_ai_proactive_action,
    track_ai_proactive_batch,
    track_ai_proactive_event,
    track_ai_request,
    track_ai_stream_result,
    track_ai_time_to_first_token,
    track_ai_tool,
    track_ai_usage,
)
from sophie_bot.metrics.background import start_background_tasks
from sophie_bot.metrics.external import (
    create_service_tracker,
    instrument_external_service,
    instrument_mongo,
    instrument_openai,
    instrument_redis,
    instrument_telegram_api,
    time_external_service,
    time_mongo_operation,
    time_openai_operation,
    time_redis_operation,
    time_telegram_api_operation,
)
from sophie_bot.metrics.middleware import MetricsMiddleware

__all__ = [
    "MetricsMiddleware",
    "start_background_tasks",
    # External service instrumentation
    "time_external_service",
    "instrument_external_service",
    "instrument_mongo",
    "instrument_redis",
    "instrument_openai",
    "instrument_telegram_api",
    "time_mongo_operation",
    "time_redis_operation",
    "time_openai_operation",
    "time_telegram_api_operation",
    "create_service_tracker",
    # AI metrics instrumentation
    "count_retries_from_messages",
    "track_ai_agent_result",
    "track_ai_request",
    "track_ai_stream_result",
    "track_ai_time_to_first_token",
    "track_ai_tool",
    "track_ai_usage",
    "track_ai_conversation",
    "track_ai_proactive_action",
    "track_ai_proactive_batch",
    "track_ai_proactive_event",
]
