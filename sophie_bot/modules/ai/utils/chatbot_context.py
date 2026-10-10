from __future__ import annotations

import datetime

import sentry_sdk
from aiogram.types import Message

from sophie_bot.modules.ai.utils.ai_model_plan import AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import modern_context_window_tokens
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.chatbot_agent import coerce_usage_limit
from sophie_bot.modules.ai.utils.chatbot_tool_history import load_chatbot_tool_history
from sophie_bot.modules.ai.utils.modern_context import ActivityCallback, ModernContext
from sophie_bot.modules.ai.utils.old_context import CHATBOT_CACHE_MESSAGE_LIMIT, OldContext
from sophie_bot.utils.feature_flags import get_value, is_enabled


async def prepare_chatbot_history(
    message: Message,
    context: SophieAIToolContext,
    *,
    model_plan: AIModelPlan | None = None,
    request_context: str = "",
    on_activity: ActivityCallback | None = None,
) -> OldContext | ModernContext:
    with sentry_sdk.start_span(op="ai.context", name="Prepare chatbot history") as span:
        history: OldContext | ModernContext
        if await is_enabled("ai_chatbot_modern_context", chat_tid=context.chat_tid, redis=context.services.redis):
            configured_budget = int(
                await get_value(
                    "ai_chatbot_modern_context_tokens", chat_tid=context.chat_tid, redis=context.services.redis
                )
            )
            output_budget = (
                coerce_usage_limit(
                    await get_value(
                        "ai_chatbot_response_tokens_limit", chat_tid=context.chat_tid, redis=context.services.redis
                    )
                )
                or 2048
            )
            if model_plan is None:
                raise ValueError("Modern context requires a model plan with registered context sizes")
            capacity = modern_context_window_tokens(model_plan)
            history = await ModernContext.build(
                message,
                context,
                token_budget=min(configured_budget, capacity - output_budget - 4096),
                request_context=request_context,
                on_activity=on_activity,
            )
            span.set_data("ai.mode", context.mode.value)
            span.set_data("ai.history_messages", len(history.message_history))
            span.set_data("ai.modern_context", True)
            return history
        history = OldContext(services=context.services)
        max_age_minutes = int(
            await get_value(
                "ai_chatbot_history_max_age_minutes",
                chat_tid=context.chat_tid,
                redis=context.services.redis,
            )
        )
        max_age = datetime.timedelta(minutes=max_age_minutes) if max_age_minutes > 0 else None

        tool_exchanges = await load_chatbot_tool_history(context.chat_tid, redis=context.services.redis)
        await history.add_from_cache(
            context.chat_tid,
            limit=CHATBOT_CACHE_MESSAGE_LIMIT,
            fold_background=True,
            max_age=max_age,
            tool_exchanges=tool_exchanges,
        )

        await history.add_from_message(message, custom_text=context.user_text, on_activity=on_activity)
        history.apply_context_block()
        span.set_data("ai.mode", context.mode.value)
        span.set_data("ai.history_messages", len(history.message_history))
        span.set_data("ai.tool_history_runs", len(tool_exchanges))
        span.set_data("ai.max_age_enabled", max_age is not None)
        return history
