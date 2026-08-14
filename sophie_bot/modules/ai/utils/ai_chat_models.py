from __future__ import annotations

from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode
from sophie_bot.modules.ai.utils.ai_model_factory import build_purpose_plan
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelPlan
from sophie_bot.utils.feature_flags import FeatureType, get_service_tier, get_value
from sophie_bot.utils.logger import log

# The per-chat/global feature-flag override for each purpose. Empty means "use the mode's model";
# a value pins that purpose to a specific model. Also the canonical map the REST API exposes so the
# panel can offer per-chat model overrides.
MODEL_OVERRIDE_FLAG_BY_PURPOSE: dict[AIModelPurpose, FeatureType] = {
    AIModelPurpose.chatbot: "ai_chatbot_model",
    AIModelPurpose.translation: "ai_translation_model",
    AIModelPurpose.filters: "ai_filter_handler_model",
    AIModelPurpose.summary: "ai_summary_model",
    AIModelPurpose.research: "ai_research_model",
    AIModelPurpose.moderation_reason: "ai_moderation_reason_model",
    AIModelPurpose.sophie_inspect: "ai_sophie_inspect_model",
}

# The feature-flag service tier for each purpose, used when the resolved role sets none of its own.
SERVICE_TIER_FLAG_BY_PURPOSE: dict[AIModelPurpose, FeatureType] = {
    AIModelPurpose.chatbot: "ai_chatbot_service_tier",
    AIModelPurpose.translation: "ai_translations_service_tier",
    AIModelPurpose.filters: "ai_filters_service_tier",
    AIModelPurpose.summary: "ai_chat_summaries_service_tier",
    AIModelPurpose.research: "ai_research_service_tier",
}


async def _get_override_name(purpose: AIModelPurpose, chat_tid: int | None) -> str:
    flag = MODEL_OVERRIDE_FLAG_BY_PURPOSE.get(purpose)
    return str(await get_value(flag, chat_tid=chat_tid)) if flag else ""


async def get_chat_model_plan(
    purpose: AIModelPurpose,
    chat_iid: PydanticObjectId | None,
    chat_tid: int | None = None,
    mode: AIMode | None = None,
) -> AIModelPlan:
    """The ordered models that may serve a purpose in a chat, best first.

    Without a chat the mode is unknown, so the default tier from ``get_chat_mode`` is used. The
    operator override flag leads the plan when set — see :func:`build_purpose_plan`.
    """
    if mode is None:
        mode = await get_chat_mode(chat_iid) if chat_iid else AIMode.support

    plan = await build_purpose_plan(mode, purpose, await _get_override_name(purpose, chat_tid), chat_tid=chat_tid)

    log.debug(f"{purpose.value} models for chat {chat_iid}: {', '.join(plan.model_names)}", mode=mode.value)

    return plan


async def resolve_chat_service_tier(
    purpose: AIModelPurpose,
    chat_iid: PydanticObjectId | None,
    chat_tid: int | None = None,
    mode: AIMode | None = None,
) -> str | None:
    """The feature-flag service tier for a purpose in a chat, or ``None`` when it has no flag.

    This is only the default. A role's own tier belongs to the model that role names, so it travels
    on :class:`~sophie_bot.modules.ai.utils.ai_model_plan.AIModelCandidate` and is applied to the
    candidate actually being attempted — failover would otherwise bill a second model at the
    primary's tier. ``"none"`` on a role means "no tier", overriding this default.
    """
    flag = SERVICE_TIER_FLAG_BY_PURPOSE.get(purpose)
    return await get_service_tier(flag, chat_tid=chat_tid) if flag else None


async def get_chat_default_model_plan(
    chat_iid: PydanticObjectId | None, chat_tid: int | None = None, mode: AIMode | None = None
) -> AIModelPlan:
    return await get_chat_model_plan(AIModelPurpose.chatbot, chat_iid, chat_tid, mode)


async def get_chat_translations_model_plan(
    chat_iid: PydanticObjectId, chat_tid: int | None = None, mode: AIMode | None = None
) -> AIModelPlan:
    return await get_chat_model_plan(AIModelPurpose.translation, chat_iid, chat_tid, mode)


async def get_chat_filters_model_plan(
    chat_iid: PydanticObjectId | None, chat_tid: int | None = None, mode: AIMode | None = None
) -> AIModelPlan:
    return await get_chat_model_plan(AIModelPurpose.filters, chat_iid, chat_tid, mode)


async def get_chat_summary_model_plan(chat_iid: PydanticObjectId, chat_tid: int | None = None) -> AIModelPlan:
    return await get_chat_model_plan(AIModelPurpose.summary, chat_iid, chat_tid)


async def get_moderation_reason_model_plan(
    chat_iid: PydanticObjectId | None, chat_tid: int | None = None
) -> AIModelPlan:
    return await get_chat_model_plan(AIModelPurpose.moderation_reason, chat_iid, chat_tid)


async def get_chat_research_model_plan(chat_iid: PydanticObjectId | None, chat_tid: int | None = None) -> AIModelPlan:
    return await get_chat_model_plan(AIModelPurpose.research, chat_iid, chat_tid)
