from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from beanie import PydanticObjectId
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from sophie_bot.modules.ai.utils.ai_run import AIAgentResult, AIRequestOptions, run_ai_structured
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.utils.ai_features import AIFeature
from sophie_bot.utils.feature_flags import FeatureType
from sophie_bot.utils.feature_flags import get_service_tier

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class AITextTask:
    instructions: str
    feature: AIFeature | None = None
    service_tier_feature_key: FeatureType | None = None
    usage_limits: UsageLimits | None = None


@dataclass(frozen=True, slots=True)
class AIStructuredTask(Generic[OutputT]):
    instructions: str
    output_type: type[OutputT]
    feature: AIFeature | None = None
    service_tier_feature_key: FeatureType | None = None
    usage_limits: UsageLimits | None = None
    model_settings: Mapping[str, object] | None = None


def build_task_history(task: AIStructuredTask[OutputT] | AITextTask, user_prompt: str) -> AIMessageHistory:
    history = AIMessageHistory()
    history.add_system(task.instructions)
    history.prompt = [user_prompt]
    return history


async def run_structured_task(
    task: AIStructuredTask[OutputT],
    model: Model,
    history: AIMessageHistory,
    chat_iid: PydanticObjectId | None = None,
    user_tracking_id: object | None = None,
    chat_tid: int | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
) -> AIAgentResult[OutputT]:
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
    agent = cast(Agent[None, OutputT], Agent(model, output_type=task.output_type))
    result = await run_ai_structured(
        agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        usage_limits=task.usage_limits,
        request_options=request_options,
        model_settings=task.model_settings,
    )
    if chat_iid is not None and task.feature is not None and result.usage and result.usage.total_tokens:
        await charge_ai_usage(chat_iid, task.feature, model, result.usage)
    return result


async def run_structured_prompt_task(
    task: AIStructuredTask[OutputT],
    model: Model,
    user_prompt: str,
    chat_iid: PydanticObjectId | None = None,
    user_tracking_id: object | None = None,
    chat_tid: int | None = None,
    session_id: str | None = None,
    service_tier: str | None = None,
) -> AIAgentResult[OutputT]:
    return await run_structured_task(
        task,
        model,
        build_task_history(task, user_prompt),
        chat_iid=chat_iid,
        user_tracking_id=user_tracking_id,
        chat_tid=chat_tid,
        session_id=session_id,
        service_tier=service_tier,
    )
