from __future__ import annotations

from beanie import PydanticObjectId
from pydantic_ai.models import Model

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_catalog import resolve_model_name
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode
from sophie_bot.modules.ai.utils.ai_model_factory import get_ai_model
from sophie_bot.utils.feature_flags import FeatureType, get_value
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


async def _get_override(purpose: AIModelPurpose, chat_tid: int | None) -> Model | None:
    flag = MODEL_OVERRIDE_FLAG_BY_PURPOSE.get(purpose)
    if flag is None:
        return None
    override_name = str(await get_value(flag, chat_tid=chat_tid))
    if not override_name:
        return None
    log.debug(f"{purpose.value} model override: {override_name}")
    return get_ai_model(override_name)


async def _get_chat_model(
    purpose: AIModelPurpose,
    chat_iid: PydanticObjectId | None,
    chat_tid: int | None = None,
    mode: AIMode | None = None,
) -> Model:
    """Resolve a per-chat purpose: the operator override flag first, then the chat's AI mode.

    Without a chat the mode is unknown, so the default tier from ``get_chat_mode`` is used.
    """
    if override := await _get_override(purpose, chat_tid):
        return override

    if mode is None:
        mode = await get_chat_mode(chat_iid) if chat_iid else AIMode.support
    model_name = await resolve_model_name(mode, purpose)

    log.debug(f"{purpose.value} model for chat {chat_iid}: {model_name}", mode=mode.value)

    return get_ai_model(model_name)


async def get_chat_default_model(
    chat_iid: PydanticObjectId, chat_tid: int | None = None, mode: AIMode | None = None
) -> Model:
    return await _get_chat_model(AIModelPurpose.chatbot, chat_iid, chat_tid, mode)


async def get_chat_translations_model(
    chat_iid: PydanticObjectId, chat_tid: int | None = None, mode: AIMode | None = None
) -> Model:
    return await _get_chat_model(AIModelPurpose.translation, chat_iid, chat_tid, mode)


async def get_chat_filters_model(
    chat_iid: PydanticObjectId | None, chat_tid: int | None = None, mode: AIMode | None = None
) -> Model:
    return await _get_chat_model(AIModelPurpose.filters, chat_iid, chat_tid, mode)


async def get_chat_summary_model(chat_iid: PydanticObjectId, chat_tid: int | None = None) -> Model:
    return await _get_chat_model(AIModelPurpose.summary, chat_iid, chat_tid)


async def get_moderation_reason_model(chat_iid: PydanticObjectId | None, chat_tid: int | None = None) -> Model:
    return await _get_chat_model(AIModelPurpose.moderation_reason, chat_iid, chat_tid)


async def get_chat_research_model(chat_iid: PydanticObjectId | None, chat_tid: int | None = None) -> Model:
    return await _get_chat_model(AIModelPurpose.research, chat_iid, chat_tid)
