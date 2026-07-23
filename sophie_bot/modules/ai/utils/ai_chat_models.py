from __future__ import annotations

from beanie import PydanticObjectId
from pydantic_ai.models import Model

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode
from sophie_bot.modules.ai.utils.ai_model_factory import get_ai_model
from sophie_bot.modules.ai.utils.ai_model_registry import (
    ModelPurpose,
    get_default_summary_model_name,
    get_model_name,
)
from sophie_bot.utils.feature_flags import FeatureType, get_value
from sophie_bot.utils.logger import log

_OVERRIDE_FLAG_BY_PURPOSE: dict[ModelPurpose, FeatureType] = {
    "chatbot": "ai_chatbot_model",
    "translation": "ai_translation_model",
    "filters": "ai_filter_handler_model",
}


async def _get_chat_model(
    purpose: ModelPurpose,
    chat_iid: PydanticObjectId | None,
    chat_tid: int | None = None,
    mode: AIMode | None = None,
) -> Model:
    """Resolve a chat's model for a purpose: the operator override flag first, then its AI mode.

    Without a chat the mode is unknown, so the default tier from ``get_chat_mode`` is used.
    """
    override_name = str(await get_value(_OVERRIDE_FLAG_BY_PURPOSE[purpose], chat_tid=chat_tid))
    if override_name:
        log.debug(f"{purpose} model override for chat {chat_iid}: {override_name}")
        return get_ai_model(override_name)

    if mode is None:
        mode = await get_chat_mode(chat_iid) if chat_iid else AIMode.support
    model_name = get_model_name(mode, purpose)

    log.debug(f"{purpose} model for chat {chat_iid}: {model_name}", mode=mode.value)

    return get_ai_model(model_name)


async def get_chat_default_model(
    chat_iid: PydanticObjectId, chat_tid: int | None = None, mode: AIMode | None = None
) -> Model:
    return await _get_chat_model("chatbot", chat_iid, chat_tid, mode)


async def get_chat_translations_model(
    chat_iid: PydanticObjectId, chat_tid: int | None = None, mode: AIMode | None = None
) -> Model:
    return await _get_chat_model("translation", chat_iid, chat_tid, mode)


async def get_chat_filters_model(
    chat_iid: PydanticObjectId | None, chat_tid: int | None = None, mode: AIMode | None = None
) -> Model:
    return await _get_chat_model("filters", chat_iid, chat_tid, mode)


async def get_chat_summary_model(chat_iid: PydanticObjectId, chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_summary_model", chat_tid=chat_tid)) or get_default_summary_model_name()

    log.debug(f"Summary model for chat {chat_iid}: {model_name}")

    return get_ai_model(model_name)
