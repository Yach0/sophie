from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from beanie import PydanticObjectId
from pydantic import BaseModel
from pydantic_ai import Agent

from sophie_bot.modules.ai.utils.ai_model_plan import AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult, AIRequestOptions, run_ai_structured
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.utils.ai_features import AIFeature
from sophie_bot.utils.feature_flags import FeatureType, get_service_tier


@dataclass(frozen=True, slots=True)
class AIStructuredTask[OutputT: BaseModel]:
    output_type: type[OutputT]
    feature: AIFeature | None = None
    service_tier_feature_key: FeatureType | None = None
    model_settings: Mapping[str, object] | None = None


async def run_structured_task[OutputT: BaseModel](
    task: AIStructuredTask[OutputT],
    model_plan: AIModelPlan,
    history: AIMessageHistory,
    chat_iid: PydanticObjectId | None = None,
    user_tracking_id: object | None = None,
    chat_tid: int | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
) -> AIAgentResult[OutputT]:
    """Run a structured task over its purpose's model plan.

    Every structured mode — translation, filters, summaries, moderation reasons, note titles —
    arrives here, so the plan's failover applies to all of them without any of them looping itself.
    """
    resolved_service_tier = service_tier or (
        await get_service_tier(task.service_tier_feature_key, chat_tid=chat_tid)
        if task.service_tier_feature_key is not None
        else None
    )
    request_options = AIRequestOptions(
        user_tracking_id=user_tracking_id if user_tracking_id is not None else chat_iid,
        session_id=session_id,
        service_tier=resolved_service_tier,
    )
    agent = cast(Agent[None, OutputT], Agent(model_plan.primary, output_type=task.output_type))
    result = await run_ai_structured(
        agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        request_options=request_options,
        model_settings=task.model_settings,
        model_plan=model_plan,
    )
    if chat_iid is not None and task.feature is not None and result.usage and result.usage.total_tokens:
        await charge_ai_usage(chat_iid, task.feature, result.served_model or model_plan.primary, result.usage)
    return result
