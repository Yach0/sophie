from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.openrouter import OpenRouterModelSettings

from sophie_bot.modules.ai.utils.ai_model_registry import AI_MODEL_REGISTRY, AI_MODELS_BY_NAME
from sophie_bot.modules.ai.utils.ai_providers import AI_PROVIDERS

_ai_models: dict[str, Model] | None = None
_filter_handler_model_name = "openai/gpt-5-nano"
_moderation_reason_model_name = "mistralai/mistral-small-2603"


def _build_model(model_name: str) -> Model:
    model_metadata = AI_MODELS_BY_NAME[model_name]
    provider_factory = AI_PROVIDERS[model_metadata.provider.name]
    provider_instance = provider_factory()
    settings = OpenRouterModelSettings(**model_metadata.extra_params) if model_metadata.extra_params else None
    return OpenRouterModel(model_metadata.name, provider=provider_instance, settings=settings)


def _get_ai_models() -> dict[str, Model]:
    global _ai_models
    if _ai_models is None:
        _ai_models = {model.name: _build_model(model.name) for model in AI_MODEL_REGISTRY}
    return _ai_models


class _LazyAIModels(dict):
    def __getitem__(self, key: str) -> Model:
        return _get_ai_models()[key]

    def __contains__(self, key: object) -> bool:
        return key in _get_ai_models()

    def keys(self):
        return _get_ai_models().keys()

    def values(self):
        return _get_ai_models().values()

    def items(self):
        return _get_ai_models().items()

    def get(self, key, default=None):
        return _get_ai_models().get(key, default)


class _LazyFixedModel:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def __get__(self, obj, objtype=None):
        return _get_ai_models()[self.model_name]

    def __call__(self):
        return _get_ai_models()[self.model_name]


AI_MODELS = _LazyAIModels()
FILTER_HANDLER_MODEL = _LazyFixedModel(_filter_handler_model_name)
MODERATION_REASON_MODEL = _LazyFixedModel(_moderation_reason_model_name)
