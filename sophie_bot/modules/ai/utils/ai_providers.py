import sys
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from sophie_bot.config import CONFIG


class AIProviders(str, Enum):
    auto = "auto"
    anthropic = "anthropic"
    google = "google"
    mistral = "mistral"
    openai = "openai"
    zai = "zai"


# Check if we're in testing mode
TESTING = "pytest" in sys.modules or __import__("os").environ.get("TESTING") == "1"

# Lazy provider initialization
_openrouter_provider = None
_mistral_provider = None


def _get_openrouter_provider():
    global _openrouter_provider
    if _openrouter_provider is None:
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        _openrouter_provider = OpenRouterProvider(api_key=CONFIG.openrouter_api_key or "")
    return _openrouter_provider


def _get_mistral_provider():
    global _mistral_provider
    if _mistral_provider is None:
        from pydantic_ai.providers.mistral import MistralProvider

        _mistral_provider = MistralProvider(api_key=CONFIG.mistral_api_key or "")
    return _mistral_provider


if TESTING:
    AI_PROVIDERS = {}
else:
    AI_PROVIDERS = {
        AIProviders.anthropic.name: _get_openrouter_provider,
        AIProviders.google.name: _get_openrouter_provider,
        AIProviders.mistral.name: _get_mistral_provider,
        AIProviders.openai.name: _get_openrouter_provider,
        AIProviders.zai.name: _get_openrouter_provider,
    }
