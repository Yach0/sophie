from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import cast

from beanie import PydanticObjectId
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.usage import RunUsage, UsageLimits
from redis.asyncio import Redis

from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, ai_error_scope
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult, AIRequestOptions, run_ai_structured
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.old_context import OldContext
from sophie_bot.utils.ai_features import AIFeature
from sophie_bot.utils.feature_flags import FeatureType, get_service_tier


@dataclass(frozen=True, slots=True)
class AIStructuredTask[OutputT: BaseModel]:
    output_type: type[OutputT]
    name: str
    feature: AIFeature | None = None
    service_tier_feature_key: FeatureType | None = None
    model_settings: Mapping[str, object] | None = None


async def run_structured_task[OutputT: BaseModel](
    task: AIStructuredTask[OutputT],
    model_plan: AIModelPlan,
    history: OldContext,
    chat_iid: PydanticObjectId | None = None,
    chat_tid: int | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
    *,
    redis: Redis,
    single_model: bool = False,
    usage_limits: UsageLimits | None = None,
    on_response: Callable[[Model, ModelResponse], Awaitable[None]] | None = None,
) -> AIAgentResult[OutputT]:
    """Run a structured task over its purpose's model plan.

    Every structured mode — translation, filters, summaries, moderation reasons, note titles —
    arrives here, so the plan's failover applies to all of them without any of them looping itself.
    """
    resolved_service_tier = service_tier or (
        await get_service_tier(
            task.service_tier_feature_key,
            chat_tid=chat_tid,
            redis=redis,
        )
        if task.service_tier_feature_key is not None
        else None
    )
    request_options = AIRequestOptions(
        user_tracking_id=chat_iid,
        session_id=session_id,
        service_tier=resolved_service_tier,
        single_model=single_model,
    )
    agent = cast(
        Agent[None, OutputT],
        Agent(model_plan.primary, name=task.name, output_type=task.output_type),
    )

    async def observe(
        _ctx: RunContext[None], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        if on_response is not None:
            await on_response(request_context.model, response)
            if chat_iid is not None and task.feature is not None and response.usage is not None:
                usage = RunUsage()
                usage.incr(response.usage)
                await charge_ai_usage(chat_iid, task.feature, request_context.model, usage, redis=redis)
        return response

    with ai_error_scope(AIErrorContext(operation=task.name, model_name=model_plan.primary.model_name)):
        result = await run_ai_structured(
            agent,
            user_prompt=history.prompt,
            message_history=history.message_history,
            request_options=request_options,
            model_settings=task.model_settings,
            model_plan=model_plan,
            usage_limits=usage_limits,
            capabilities=[Hooks(after_model_request=observe)] if on_response is not None else [],
        )
    if (
        on_response is None
        and chat_iid is not None
        and task.feature is not None
        and result.usage
        and result.usage.total_tokens
    ):
        await charge_ai_usage(
            chat_iid,
            task.feature,
            result.served_model or model_plan.primary,
            result.usage,
            redis=redis,
        )

    return result
