from __future__ import annotations

from beanie import PydanticObjectId
from pydantic_ai.models import Model

from sophie_bot.db.models.ai.ai_provider import AIProviderModel
from sophie_bot.modules.ai.utils.ai_models import (
    AI_MODELS,
    AIProviders,
    get_default_model_name,
)
from sophie_bot.utils.feature_flags import _DEFAULT_STATES, get_value
from sophie_bot.utils.logger import log


async def get_chat_default_model(chat_id: PydanticObjectId) -> Model:
    provider_name = await AIProviderModel.get_provider_name(chat_id)
    provider_key = provider_name or AIProviders.auto.name
    default_model_name = get_default_model_name(provider_key)

    log.debug(f"Default model for chat {chat_id}: {default_model_name}", provider_name=provider_name)

    return AI_MODELS[default_model_name]


async def get_chat_translations_model(chat_id: PydanticObjectId) -> Model:
    provider_name = await AIProviderModel.get_provider_name(chat_id)
    provider_key = provider_name or AIProviders.auto.name
    default_model_name = get_default_model_name(provider_key, translation=True)

    log.debug(f"Default model for chat {chat_id}: {default_model_name}", provider_name=provider_name)

    return AI_MODELS[default_model_name]


async def get_chat_summary_model(chat_id: PydanticObjectId) -> Model:
    feature_model_name = str(await get_value("ai_summary_model"))
    default_model_name = _DEFAULT_STATES["ai_summary_model"]
    model_name = feature_model_name
    if feature_model_name == default_model_name:
        model_name = await AIProviderModel.get_summary_model_name(chat_id)

    log.debug(f"Summary model for chat {chat_id}: {model_name}")

    return AI_MODELS[model_name]
