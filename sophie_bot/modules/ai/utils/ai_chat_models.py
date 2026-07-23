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

_OVERRIDE_FLAG_BY_PURPOSE: dict[AIModelPurpose, FeatureType] = {
    AIModelPurpose.chatbot: "ai_chatbot_model",
    AIModelPurpose.translation: "ai_translation_model",
    AIModelPurpose.filters: "ai_filter_handler_model",
    AIModelPurpose.summary: "ai_summary_model",
}


async def _get_override(purpose: AIModelPurpose, chat_tid: int | None) -> Model | None:
    override_name = str(await get_value(_OVERRIDE_FLAG_BY_PURPOSE[purpose], chat_tid=chat_tid))
    if not override_name:
        return None
    log.debug(f"{purpose.value} model override: {override_name}")
    return get_ai_model(override_name)


async def _get_global_model(purpose: AIModelPurpose, chat_tid: int | None = None) -> Model:
    """Resolve a purpose that is the same for every chat regardless of its mode."""
    if override := await _get_override(purpose, chat_tid):
        return override
    return get_ai_model(await resolve_model_name(None, purpose))


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
    """Summaries are not per-chat: every mode uses the same model unless an operator overrides it."""
    del chat_iid
    return await _get_global_model(AIModelPurpose.summary, chat_tid)


async def get_moderation_reason_model() -> Model:
    return get_ai_model(await resolve_model_name(None, AIModelPurpose.moderation_reason))
