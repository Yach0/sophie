from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.settings import ModelSettings

from sophie_bot.modules.ai.utils.ai_model_registry import (
    SophieAIModel,
    api_model_name,
    get_model_overrides,
)
from sophie_bot.modules.ai.utils.ai_providers import get_custom_provider, get_openrouter_provider
from sophie_bot.utils.feature_flags import get_value

_ai_models: dict[str, Model] = {}
_moderation_reason_model_name = "mistralai/mistral-small-2603"

# Default reasoning effort applied to every model to keep token costs down. Models that do not
# support reasoning simply ignore the parameter on OpenRouter; per-model extra_params win on conflict.
_DEFAULT_REASONING_EFFORT = "low"


def _build_openrouter_settings(overrides: SophieAIModel | None) -> OpenRouterModelSettings | None:
    extra_params: dict[str, object] = {}
    if overrides is None or overrides.supports_reasoning:
        extra_params["openrouter_reasoning"] = {"effort": _DEFAULT_REASONING_EFFORT}
    if overrides and overrides.extra_params:
        extra_params.update(overrides.extra_params)
    return OpenRouterModelSettings(**extra_params) if extra_params else None


def _build_custom_settings(overrides: SophieAIModel) -> ModelSettings | None:
    # OpenRouter-only keys such as openrouter_reasoning mean nothing to a plain OpenAI-compatible
    # endpoint, so only explicit per-model extra_params are forwarded here.
    return ModelSettings(**overrides.extra_params) if overrides.extra_params else None


def _build_model(model_name: str) -> Model:
    """Build a model by name.

    A model declaring a ``custom_provider`` goes to that OpenAI-compatible endpoint; every other
    name, including ad-hoc ones set through ``ai_*_model`` flags, goes to OpenRouter.
    """
    overrides = get_model_overrides(model_name)

    if overrides and overrides.custom_provider:
        return OpenAIChatModel(
            api_model_name(model_name, overrides.custom_provider),
            provider=get_custom_provider(overrides.custom_provider),
            settings=_build_custom_settings(overrides),
        )

    return OpenRouterModel(
        model_name, provider=get_openrouter_provider(), settings=_build_openrouter_settings(overrides)
    )


def get_ai_model(model_name: str) -> Model:
    if model_name not in _ai_models:
        _ai_models[model_name] = _build_model(model_name)
    return _ai_models[model_name]


class _LazyFixedModel:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def __get__(self, obj, objtype=None) -> Model:
        return get_ai_model(self.model_name)

    def __call__(self) -> Model:
        return get_ai_model(self.model_name)


async def get_proactive_replies_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_proactive_replies_model", chat_tid=chat_tid))
    return get_ai_model(model_name)


async def get_research_model(chat_tid: int | None = None) -> Model:
    model_name = str(await get_value("ai_research_model", chat_tid=chat_tid))
    return get_ai_model(model_name)


MODERATION_REASON_MODEL = _LazyFixedModel(_moderation_reason_model_name)
