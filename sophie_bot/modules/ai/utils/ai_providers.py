from __future__ import annotations

import os
import sys

from pydantic_ai.providers import Provider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from sophie_bot.modules.ai.utils.ai_catalog import CatalogProvider
from sophie_bot.modules.ai.utils.ai_telemetry import ai_span

TESTING = "pytest" in sys.modules or os.environ.get("TESTING") == "1"

# Providers reject an empty API key at construction time. Under pytest no key is configured and no
# request is ever made, so a placeholder keeps model construction working offline; outside tests an
# empty key still fails loudly instead of silently producing an unusable client.
_TESTING_API_KEY = "testing"

_providers: dict[tuple[str, str], Provider] = {}


def _api_key(configured_key: str) -> str:
    if configured_key:
        return configured_key
    return _TESTING_API_KEY if TESTING else ""


def _cached(provider: CatalogProvider | None, build) -> Provider:
    """Cache by name and key so rotating a key in the catalog builds a fresh client."""
    provider_type = "openai_compatible" if provider and provider.kind == "openai_compatible" else "openrouter"
    with ai_span("ai.provider.cache", provider_type=provider_type) as span:
        key = (provider.name, provider.api_key) if provider else ("", "")
        hit = key in _providers
        if span is not None:
            span.set_attribute("cache_hit", hit)
        if not hit:
            _providers[key] = build()
        return _providers[key]


def get_openrouter_provider(provider: CatalogProvider | None = None) -> Provider:
    api_key = _api_key(provider.api_key if provider else "")
    if not api_key:
        raise ValueError("OpenRouter API key is missing from the AI catalog; configure /op_aiprovider openrouter")
    return _cached(provider, lambda: OpenRouterProvider(api_key=api_key))


def get_openai_provider(provider: CatalogProvider) -> Provider:
    return _cached(provider, lambda: OpenAIProvider(base_url=provider.base_url, api_key=_api_key(provider.api_key)))
