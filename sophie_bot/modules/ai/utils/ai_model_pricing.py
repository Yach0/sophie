from __future__ import annotations

from math import ceil

import ujson
from httpx import AsyncClient, HTTPError

from sophie_bot.config import CONFIG
from sophie_bot.constants import AI_BASE_INPUT_PRICE_PER_MILLION, AI_BASE_OUTPUT_PRICE_PER_MILLION, AI_CREDITS_PER_TOKEN
from sophie_bot.modules.ai.utils.ai_model_registry import AI_MODELS_BY_NAME
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

ai_http_client = AsyncClient(timeout=30)
_pricing_cache_ttl_seconds = 3600.0
_PRICING_CACHE_KEY = "sophie:ai:openrouter_pricing"


async def clear_model_pricing_cache() -> None:
    await aredis.delete(_PRICING_CACHE_KEY)


async def refresh_model_pricing_cache() -> dict[str, tuple[float | None, float | None]]:
    await clear_model_pricing_cache()
    return await _load_openrouter_pricing_cache()


async def close_model_pricing_client() -> None:
    await ai_http_client.aclose()


def _openrouter_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if CONFIG.openrouter_api_key:
        headers["Authorization"] = f"Bearer {CONFIG.openrouter_api_key}"
    return headers


def _parse_price_per_million(raw_price: object) -> float | None:
    if raw_price in (None, "", 0, "0"):
        return 0.0 if raw_price in (0, "0") else None

    if not isinstance(raw_price, str | int | float):
        return None

    try:
        return float(raw_price) * 1_000_000
    except (TypeError, ValueError):
        return None


async def _load_openrouter_pricing_cache() -> dict[str, tuple[float | None, float | None]]:
    cached_data = await aredis.get(_PRICING_CACHE_KEY)
    if cached_data is not None:
        try:
            cache = ujson.loads(cached_data)
            return cache
        except (ujson.JSONDecodeError, TypeError):
            pass

    cache: dict[str, tuple[float | None, float | None]] = {}
    try:
        response = await ai_http_client.get("https://openrouter.ai/api/v1/models", headers=_openrouter_headers())
        response.raise_for_status()
    except HTTPError as err:
        log.warning("Failed to load OpenRouter pricing", error=str(err))
        return cache

    data = response.json().get("data", [])
    for item in data:
        model_name = item.get("id") or item.get("name")
        if not model_name:
            continue
        pricing = item.get("pricing") or {}
        cache[model_name] = (
            _parse_price_per_million(pricing.get("prompt") or pricing.get("input") or item.get("input_price")),
            _parse_price_per_million(pricing.get("completion") or pricing.get("output") or item.get("output_price")),
        )

    serialized = ujson.dumps(cache)
    await aredis.set(_PRICING_CACHE_KEY, serialized)
    await aredis.expire(_PRICING_CACHE_KEY, int(_pricing_cache_ttl_seconds))

    return cache


async def get_model_pricing(model_name: str) -> tuple[float | None, float | None]:
    model_metadata = AI_MODELS_BY_NAME.get(model_name)
    if model_metadata and model_metadata.input_price is not None and model_metadata.output_price is not None:
        return model_metadata.input_price, model_metadata.output_price

    pricing_cache = await _load_openrouter_pricing_cache()
    fallback_input_price, fallback_output_price = pricing_cache.get(model_name, (None, None))
    if not model_metadata:
        return fallback_input_price, fallback_output_price

    return (
        model_metadata.input_price if model_metadata.input_price is not None else fallback_input_price,
        model_metadata.output_price if model_metadata.output_price is not None else fallback_output_price,
    )


async def estimate_model_credit_cost(
    model_name: str,
    total_tokens: int,
    input_tokens: int | None,
    output_tokens: int | None,
) -> int:
    input_price, output_price = await get_model_pricing(model_name)
    if input_price is None and output_price is None:
        return ceil(total_tokens / AI_CREDITS_PER_TOKEN)

    normalized_input_cost = 0.0
    normalized_output_cost = 0.0

    if input_tokens:
        effective_input_price = input_price if input_price is not None else AI_BASE_INPUT_PRICE_PER_MILLION
        normalized_input_cost = (input_tokens / AI_CREDITS_PER_TOKEN) * (
            effective_input_price / AI_BASE_INPUT_PRICE_PER_MILLION
        )

    if output_tokens:
        effective_output_price = output_price if output_price is not None else AI_BASE_OUTPUT_PRICE_PER_MILLION
        normalized_output_cost = (output_tokens / AI_CREDITS_PER_TOKEN) * (
            effective_output_price / AI_BASE_OUTPUT_PRICE_PER_MILLION
        )

    return max(ceil(normalized_input_cost + normalized_output_cost), 1)
