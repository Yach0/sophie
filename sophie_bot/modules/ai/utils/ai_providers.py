from __future__ import annotations

import os
import sys
from enum import Enum

from pydantic_ai.providers import Provider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from sophie_bot.config import CONFIG


class AIProviders(str, Enum):
    auto = "auto"
    anthropic = "anthropic"
    google = "google"
    mistral = "mistral"
    openai = "openai"
    free = "free"


TESTING = "pytest" in sys.modules or os.environ.get("TESTING") == "1"

# Providers reject an empty API key at construction time. Under pytest no key is configured and no
# request is ever made, so a placeholder keeps model construction working offline; outside tests an
# empty key still fails loudly instead of silently producing an unusable client.
_TESTING_API_KEY = "testing"

_openrouter_provider: Provider | None = None
_custom_providers: dict[str, Provider] = {}

# Names of runtime-configured OpenAI-compatible providers (see CONFIG.custom_providers). Models opt
# into one explicitly via SophieAIModel.custom_provider; a name prefix alone never routes anywhere.
CUSTOM_PROVIDER_NAMES: frozenset[str] = frozenset(CONFIG.custom_providers_by_name)


def _api_key(configured_key: str | None) -> str:
    if configured_key:
        return configured_key
    return _TESTING_API_KEY if TESTING else ""


def get_openrouter_provider() -> Provider:
    global _openrouter_provider
    if _openrouter_provider is None:
        _openrouter_provider = OpenRouterProvider(api_key=_api_key(CONFIG.openrouter_api_key))
    return _openrouter_provider


def get_custom_provider(name: str) -> Provider:
    provider = _custom_providers.get(name)
    if provider is None:
        config = CONFIG.custom_providers_by_name[name]
        provider = OpenAIProvider(base_url=config.base_url, api_key=_api_key(config.api_key))
        _custom_providers[name] = provider
    return provider
