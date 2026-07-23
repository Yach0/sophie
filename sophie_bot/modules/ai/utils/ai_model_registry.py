from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from sophie_bot.constants import DEFAULT_AI_SUMMARY_MODEL
from sophie_bot.modules.ai.utils.ai_providers import CUSTOM_PROVIDER_NAMES, AIProviders
from sophie_bot.utils.logger import log

ModelPurpose = Literal["chatbot", "translation", "filters"]


@dataclass(frozen=True)
class SophieAIModel:
    provider: AIProviders
    name: str
    supports_reasoning: bool = True
    default_for_chatbot: bool = False
    default_for_translation: bool = False
    default_for_summary: bool = False
    default_for_filters: bool = False
    # When set, the model is sent to this runtime-configured OpenAI-compatible provider instead of
    # OpenRouter, with the "<custom_provider>/" prefix stripped from ``name``. Models naming a
    # provider that is not configured are dropped from the registry rather than sent to OpenRouter,
    # where their name does not exist.
    custom_provider: str | None = None
    extra_params: dict[str, Any] | None = None

    @property
    def api_model_name(self) -> str:
        if self.custom_provider:
            return self.name.removeprefix(f"{self.custom_provider}/")
        return self.name


AI_PROVIDER_TO_NAME = {
    AIProviders.auto.name: "Auto",
    AIProviders.anthropic.name: "Anthropic Claude",
    AIProviders.google.name: "Google Gemini",
    AIProviders.mistral.name: "Mistral AI",
    AIProviders.openai.name: "OpenAI ChatGPT",
    AIProviders.free.name: "Free",
}

_DECLARED_MODELS: list[SophieAIModel] = [
    SophieAIModel(AIProviders.google, "google/gemini-2.5-pro"),
    SophieAIModel(AIProviders.google, "google/gemini-2.5-flash"),
    SophieAIModel(AIProviders.google, "google/gemini-3-flash-preview", default_for_chatbot=True),
    SophieAIModel(
        AIProviders.google,
        "google/gemini-3.1-flash-lite-preview",
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.anthropic, "anthropic/claude-sonnet-4.5"),
    SophieAIModel(
        AIProviders.anthropic,
        "anthropic/claude-haiku-4.5",
        default_for_chatbot=True,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.anthropic, "anthropic/claude-3.5-haiku"),
    SophieAIModel(AIProviders.mistral, "mistralai/mistral-large"),
    SophieAIModel(AIProviders.mistral, "mistralai/mistral-medium-3.1", default_for_chatbot=True),
    SophieAIModel(
        AIProviders.mistral,
        "mistralai/mistral-small-2603",
        default_for_chatbot=True,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.mistral, "mistralai/magistral-small-2506"),
    SophieAIModel(AIProviders.mistral, "mistralai/magistral-medium-2506"),
    SophieAIModel(AIProviders.mistral, "mistralai/codestral-2508"),
    SophieAIModel(AIProviders.mistral, "mistralai/pixtral-12b"),
    SophieAIModel(AIProviders.mistral, "mistralai/mistral-small-3.2-24b-instruct"),
    SophieAIModel(AIProviders.openai, "openai/gpt-4o-mini"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5-mini"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5-nano"),
    SophieAIModel(
        AIProviders.openai,
        "openai/gpt-5.6-luna",
        default_for_chatbot=True,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.1"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.2-chat"),
    SophieAIModel(AIProviders.openai, "openai/gpt-oss-120b"),
    SophieAIModel(AIProviders.openai, "openai/gpt-oss-20b"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.4"),
    SophieAIModel(
        AIProviders.openai,
        DEFAULT_AI_SUMMARY_MODEL,
        default_for_summary=True,
        extra_params={"openrouter_reasoning": {"effort": "low"}},
    ),
    SophieAIModel(
        AIProviders.openai,
        "openai/gpt-5.4-mini",
        default_for_chatbot=True,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.4-nano"),
    SophieAIModel(AIProviders.openai, "xiaomi/mimo-v2.5-pro"),
    SophieAIModel(
        AIProviders.free,
        "openrouter/auto",
        default_for_chatbot=True,
    ),
    SophieAIModel(
        AIProviders.free,
        "qwencloud/qwen3-vl-flash",
        default_for_translation=True,
        default_for_filters=True,
        custom_provider="qwencloud",
    ),
]


def _is_available(model: SophieAIModel) -> bool:
    return model.custom_provider is None or model.custom_provider in CUSTOM_PROVIDER_NAMES


_UNAVAILABLE_MODELS = tuple(model.name for model in _DECLARED_MODELS if not _is_available(model))
if _UNAVAILABLE_MODELS:
    log.warning(
        "AI models dropped from the registry: their custom provider is not configured",
        models=_UNAVAILABLE_MODELS,
    )

AI_MODEL_REGISTRY: list[SophieAIModel] = [model for model in _DECLARED_MODELS if _is_available(model)]

AI_MODELS_BY_NAME: dict[str, SophieAIModel] = {model.name: model for model in AI_MODEL_REGISTRY}

_provider_to_models: dict[str, list[SophieAIModel]] = defaultdict(list)
for ai_model in AI_MODEL_REGISTRY:
    _provider_to_models[ai_model.provider.name].append(ai_model)

PROVIDER_TO_MODELS: Mapping[str, tuple[SophieAIModel, ...]] = {
    provider_name: tuple(models) for provider_name, models in _provider_to_models.items()
}


def get_provider_models(provider_name: str) -> tuple[SophieAIModel, ...]:
    return PROVIDER_TO_MODELS.get(provider_name, ())


_AUTO_MODEL_BY_PURPOSE: Mapping[ModelPurpose, str] = {
    "chatbot": "google/gemini-3-flash-preview",
    "translation": "mistralai/mistral-small-2603",
    "filters": "mistralai/pixtral-12b",
}

_DEFAULT_FLAG_BY_PURPOSE: Mapping[ModelPurpose, Callable[[SophieAIModel], bool]] = {
    "chatbot": lambda model: model.default_for_chatbot,
    "translation": lambda model: model.default_for_translation,
    "filters": lambda model: model.default_for_filters,
}


def _get_default_model(provider_name: str, purpose: ModelPurpose) -> SophieAIModel | None:
    if provider_name == AIProviders.auto.name:
        return AI_MODELS_BY_NAME.get(_AUTO_MODEL_BY_PURPOSE[purpose])

    is_default = _DEFAULT_FLAG_BY_PURPOSE[purpose]
    models = get_provider_models(provider_name)
    for model in models:
        if is_default(model):
            return model

    return models[0] if models else None


def get_default_model_name(provider_name: str, purpose: ModelPurpose = "chatbot") -> str:
    model = _get_default_model(provider_name, purpose)
    if model:
        return model.name

    fallback_model = _get_default_model(AIProviders.auto.name, purpose)
    if not fallback_model:
        raise ValueError(f"No default AI model configured for provider {provider_name}")
    return fallback_model.name


def get_default_summary_model_name() -> str:
    for model in AI_MODEL_REGISTRY:
        if model.default_for_summary:
            return model.name
    return DEFAULT_AI_SUMMARY_MODEL
