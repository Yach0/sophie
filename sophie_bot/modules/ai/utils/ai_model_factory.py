from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings

from sophie_bot.modules.ai.utils.ai_model_registry import AI_MODEL_REGISTRY, AI_MODELS_BY_NAME, SophieAIModel
from sophie_bot.modules.ai.utils.ai_providers import AI_PROVIDERS, AIProviders
from sophie_bot.utils.feature_flags import get_value

_ai_models: dict[str, Model] | None = None
_moderation_reason_model_name = "mistralai/mistral-small-2603"

# Default reasoning effort applied to every model to keep token costs down. Models that do not
# support reasoning simply ignore the parameter on OpenRouter; per-model extra_params win on conflict.
_DEFAULT_REASONING_EFFORT = "low"


def _build_model_settings(model_metadata: SophieAIModel) -> OpenRouterModelSettings | None:
    extra_params: dict[str, object] = {}
    if model_metadata.supports_reasoning:
        extra_params["openrouter_reasoning"] = {"effort": _DEFAULT_REASONING_EFFORT}
    if model_metadata.extra_params:
        extra_params.update(model_metadata.extra_params)
    return OpenRouterModelSettings(**extra_params) if extra_params else None


def _build_registered_model(model_name: str) -> Model:
    model_metadata = AI_MODELS_BY_NAME[model_name]
    provider_factory = AI_PROVIDERS[model_metadata.provider.name]
    provider_instance = provider_factory()
    settings = _build_model_settings(model_metadata)
    return OpenRouterModel(model_metadata.name, provider=provider_instance, settings=settings)


def _build_custom_model(model_name: str) -> Model:
    provider_factory = AI_PROVIDERS[AIProviders.openai.name]
    provider_instance = provider_factory()
    settings = OpenRouterModelSettings(openrouter_reasoning={"effort": _DEFAULT_REASONING_EFFORT})
    return OpenRouterModel(model_name, provider=provider_instance, settings=settings)


def get_ai_model(model_name: str) -> Model:
    models = _get_ai_models()
    if model_name not in models:
        models[model_name] = _build_custom_model(model_name)
    return models[model_name]


def _get_ai_models() -> dict[str, Model]:
    global _ai_models
    if _ai_models is None:
        _ai_models = {model.name: _build_registered_model(model.name) for model in AI_MODEL_REGISTRY}
    return _ai_models


class _LazyAIModels(dict):
    def __getitem__(self, key: str) -> Model:
        return get_ai_model(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str)

    def keys(self):
        return _get_ai_models().keys()

    def values(self):
        return _get_ai_models().values()

    def items(self):
        return _get_ai_models().items()

    def get(self, key, default=None):
        if not isinstance(key, str):
            return default
        return get_ai_model(key)


class _LazyFixedModel:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def __get__(self, obj, objtype=None):
        return get_ai_model(self.model_name)

    def __call__(self):
        return get_ai_model(self.model_name)


async def get_filter_handler_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_filter_handler_model", chat_tid=chat_tid))
    return get_ai_model(model_name)


async def get_proactive_replies_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_proactive_replies_model", chat_tid=chat_tid))
    return get_ai_model(model_name)


async def get_research_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_research_model", chat_tid=chat_tid))
    return get_ai_model(model_name)


AI_MODELS = _LazyAIModels()
MODERATION_REASON_MODEL = _LazyFixedModel(_moderation_reason_model_name)
