from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

from sophie_bot.modules.ai.utils.ai_providers import AIProviders


@dataclass(frozen=True)
class SophieAIModel:
    provider: AIProviders
    name: str
    title: str
    input_price: float | None = None
    output_price: float | None = None
    supports_tools: bool = True
    supports_vision: bool = False
    supports_translation: bool = True
    supports_reasoning: bool = True
    enabled: bool = True
    show_in_playground: bool = True
    default_for_chatbot: bool = False
    default_for_translation: bool = False
    default_for_summary: bool = False
    extra_params: dict[str, Any] | None = None


DEFAULT_SUMMARY_MODEL_NAME = "openai/gpt-5.5"


AI_PROVIDER_TO_NAME = {
    AIProviders.auto.name: "Auto",
    AIProviders.anthropic.name: "Anthropic Claude",
    AIProviders.google.name: "Google Gemini",
    AIProviders.mistral.name: "Mistral AI",
    AIProviders.openai.name: "OpenAI ChatGPT",
}

AVAILABLE_PROVIDER_NAMES: tuple[str, ...] = (
    AIProviders.auto.name,
    AIProviders.anthropic.name,
    AIProviders.openai.name,
    AIProviders.google.name,
    AIProviders.mistral.name,
)

AI_MODEL_REGISTRY: list[SophieAIModel] = [
    SophieAIModel(AIProviders.google, "google/gemini-2.5-pro", "Gemini 2.5 Pro"),
    SophieAIModel(AIProviders.google, "google/gemini-2.5-flash", "Gemini 2.5 Flash"),
    SophieAIModel(
        AIProviders.google, "google/gemini-3-flash-preview", "Gemini 3 Flash Preview", default_for_chatbot=True
    ),
    SophieAIModel(
        AIProviders.google,
        "google/gemini-3.1-flash-lite-preview",
        "Gemini 3.1 Flash Lite Preview",
        input_price=0.25,
        output_price=1.50,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.anthropic, "anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"),
    SophieAIModel(
        AIProviders.anthropic,
        "anthropic/claude-haiku-4.5",
        "Claude Haiku 4.5",
        default_for_chatbot=True,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.anthropic, "anthropic/claude-3.5-haiku", "Claude Haiku 3.5"),
    SophieAIModel(AIProviders.mistral, "mistralai/mistral-large", "Mistral Large"),
    SophieAIModel(AIProviders.mistral, "mistralai/mistral-medium-3.1", "Mistral Medium", default_for_chatbot=True),
    SophieAIModel(
        AIProviders.mistral,
        "mistralai/mistral-small-2603",
        "Mistral Small",
        input_price=0.15,
        output_price=0.60,
        default_for_chatbot=True,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.mistral, "mistralai/magistral-small-2506", "Magistral Small"),
    SophieAIModel(AIProviders.mistral, "mistralai/magistral-medium-2506", "Magistral Medium"),
    SophieAIModel(AIProviders.mistral, "mistralai/codestral-2508", "Codestral"),
    SophieAIModel(AIProviders.mistral, "mistralai/pixtral-12b", "Pixtral 12B"),
    SophieAIModel(AIProviders.mistral, "mistralai/mistral-small-3.2-24b-instruct", "Mistral Small 3.2 24B Instruct"),
    SophieAIModel(AIProviders.openai, "openai/gpt-4o-mini", "GPT-4o mini"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5", "GPT-5"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5-mini", "GPT-5 mini"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5-nano", "GPT-5 nano"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.1", "GPT-5.1"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.2-chat", "GPT-5.2 Chat"),
    SophieAIModel(AIProviders.openai, "openai/gpt-oss-120b", "GPT-OSS 120B"),
    SophieAIModel(AIProviders.openai, "openai/gpt-oss-20b", "GPT-OSS 20B"),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.4", "GPT-5.4", show_in_playground=False),
    SophieAIModel(
        AIProviders.openai,
        DEFAULT_SUMMARY_MODEL_NAME,
        "GPT-5.5",
        show_in_playground=False,
        default_for_summary=True,
        extra_params={"openrouter_reasoning": {"effort": "low"}},
    ),
    SophieAIModel(
        AIProviders.openai,
        "openai/gpt-5.4-mini",
        "GPT-5.4 mini",
        default_for_chatbot=True,
        default_for_translation=True,
    ),
    SophieAIModel(AIProviders.openai, "openai/gpt-5.4-nano", "GPT-5.4 nano"),
]

AI_MODELS_BY_NAME: dict[str, SophieAIModel] = {model.name: model for model in AI_MODEL_REGISTRY}
AI_MODEL_TO_PROVIDER: dict[str, str] = {model.name: model.provider.name for model in AI_MODEL_REGISTRY}
AI_MODEL_TO_SHORT_NAME: dict[str, str] = {model.name: model.title for model in AI_MODEL_REGISTRY}

_provider_to_models: dict[str, list[SophieAIModel]] = defaultdict(list)
for ai_model in AI_MODEL_REGISTRY:
    _provider_to_models[ai_model.provider.name].append(ai_model)

PROVIDER_TO_MODELS: Mapping[str, tuple[SophieAIModel, ...]] = {
    provider_name: tuple(model for model in models if model.enabled)
    for provider_name, models in _provider_to_models.items()
}


def get_provider_models(provider_name: str, *, playground_only: bool = False) -> tuple[SophieAIModel, ...]:
    models = PROVIDER_TO_MODELS.get(provider_name, ())
    if playground_only:
        return tuple(model for model in models if model.show_in_playground)
    return models


def _get_default_model(provider_name: str, *, translation: bool) -> SophieAIModel | None:
    if provider_name == AIProviders.auto.name:
        auto_model_name = "mistralai/mistral-small-2603"
        return AI_MODELS_BY_NAME.get(auto_model_name)

    models = get_provider_models(provider_name)
    for model in models:
        if translation and model.default_for_translation:
            return model
        if not translation and model.default_for_chatbot:
            return model

    return models[0] if models else None


def get_default_model_name(provider_name: str, *, translation: bool = False) -> str:
    model = _get_default_model(provider_name, translation=translation)
    if model:
        return model.name

    fallback_provider = AIProviders.auto.name
    fallback_model = _get_default_model(fallback_provider, translation=translation)
    if not fallback_model:
        raise ValueError(f"No default AI model configured for provider {provider_name}")
    return fallback_model.name


def get_default_summary_model_name() -> str:
    for model in AI_MODEL_REGISTRY:
        if model.default_for_summary:
            return model.name
    return DEFAULT_SUMMARY_MODEL_NAME
