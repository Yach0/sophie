"""Vendor SDK clients whose API keys live in the AI provider catalog.

These are the services Sophie calls with the vendor's own SDK rather than a chat-completions
client: Mistral's moderation classifier and audio transcription, and OpenAI's moderation endpoint.
Their keys are catalog rows, so `/op_aiprovider mistral ^key=...` rotates one without a redeploy —
the client is rebuilt as soon as the catalog version changes.
"""

from __future__ import annotations

from typing import Any, Final

from mistralai.client.sdk import Mistral
from openai import AsyncOpenAI

from sophie_bot.modules.ai.utils.ai_catalog import get_catalog
from sophie_bot.utils.logger import log

MISTRAL_PROVIDER_NAME: Final[str] = "mistral"
OPENAI_PROVIDER_NAME: Final[str] = "openai"

# AsyncOpenAI refuses to construct without a key. An unconfigured deployment gets a placeholder and
# fails at request time with a 401, which is visible in Sentry, rather than breaking at import.
_MISSING_OPENAI_KEY: Final[str] = "unset"

_clients: dict[tuple[str, str], Any] = {}


async def _api_key(provider_name: str) -> str:
    provider = (await get_catalog()).providers.get(provider_name)
    if provider is None or not provider.api_key:
        log.warning(
            "No API key in the AI catalog for this provider; its requests will fail",
            provider=provider_name,
        )
        return ""
    return provider.api_key


def _cached(provider_name: str, api_key: str, build) -> Any:
    """Cache by name and key, so rotating the key in the catalog builds a fresh client."""
    cache_key = (provider_name, api_key)
    if cache_key not in _clients:
        _clients[cache_key] = build()
    return _clients[cache_key]


async def get_mistral_client() -> Mistral:
    api_key = await _api_key(MISTRAL_PROVIDER_NAME)
    return _cached(MISTRAL_PROVIDER_NAME, api_key, lambda: Mistral(api_key=api_key))


async def get_openai_client() -> AsyncOpenAI:
    api_key = await _api_key(OPENAI_PROVIDER_NAME)
    return _cached(OPENAI_PROVIDER_NAME, api_key, lambda: AsyncOpenAI(api_key=api_key or _MISSING_OPENAI_KEY))
