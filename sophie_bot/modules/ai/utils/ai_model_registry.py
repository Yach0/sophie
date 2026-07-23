from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from sophie_bot.constants import DEFAULT_AI_SUMMARY_MODEL
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_providers import CUSTOM_PROVIDER_NAMES
from sophie_bot.utils.logger import log

ModelPurpose = Literal["chatbot", "translation", "filters"]

# The tier every mode falls back to when its own model is unavailable.
_FALLBACK_MODE = AIMode.support


@dataclass(frozen=True)
class SophieAIModel:
    """Per-model overrides. Models needing none of these are used by name alone."""

    supports_reasoning: bool = True
    # When set, the model is sent to this runtime-configured OpenAI-compatible provider instead of
    # OpenRouter, with the "<custom_provider>/" prefix stripped from the model name. Models naming a
    # provider that is not configured are treated as unavailable rather than sent to OpenRouter,
    # where their name does not exist.
    custom_provider: str | None = None
    extra_params: dict[str, Any] | None = None


AI_MODEL_OVERRIDES: Mapping[str, SophieAIModel] = {
    "qwencloud/qwen3-vl-flash": SophieAIModel(custom_provider="qwencloud"),
    DEFAULT_AI_SUMMARY_MODEL: SophieAIModel(extra_params={"openrouter_reasoning": {"effort": "low"}}),
}

# Which model serves which purpose in which mode. Disabled chats never resolve a model.
MODE_MODELS: Mapping[AIMode, Mapping[ModelPurpose, str]] = {
    AIMode.entertainment: {
        "chatbot": "openrouter/auto",
        "translation": "qwencloud/qwen3-vl-flash",
        "filters": "qwencloud/qwen3-vl-flash",
    },
    AIMode.moderation: {
        "chatbot": "mistralai/mistral-small-2603",
        "translation": "mistralai/mistral-small-2603",
        "filters": "openai/gpt-5-nano",
    },
    AIMode.support: {
        "chatbot": "openai/gpt-5.4-mini",
        "translation": "google/gemini-3.1-flash-lite-preview",
        "filters": "openai/gpt-5-nano",
    },
}


def get_model_overrides(model_name: str) -> SophieAIModel | None:
    return AI_MODEL_OVERRIDES.get(model_name)


def api_model_name(model_name: str, custom_provider: str) -> str:
    return model_name.removeprefix(f"{custom_provider}/")


def is_model_available(model_name: str) -> bool:
    overrides = AI_MODEL_OVERRIDES.get(model_name)
    if overrides is None or overrides.custom_provider is None:
        return True
    return overrides.custom_provider in CUSTOM_PROVIDER_NAMES


def get_model_name(mode: AIMode, purpose: ModelPurpose = "chatbot") -> str:
    """Resolve the model a mode uses for a purpose, falling back when it is unavailable."""
    model_name = MODE_MODELS.get(mode, MODE_MODELS[_FALLBACK_MODE])[purpose]
    if is_model_available(model_name):
        return model_name

    fallback_name = MODE_MODELS[_FALLBACK_MODE][purpose]
    log.warning(
        "AI model is unavailable, falling back",
        model=model_name,
        fallback=fallback_name,
        mode=mode.value,
        purpose=purpose,
    )
    return fallback_name


def get_default_summary_model_name() -> str:
    return DEFAULT_AI_SUMMARY_MODEL
