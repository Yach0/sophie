"""Migration: seed_ai_catalog

Description:
    Seeds the AI provider and model catalog from what used to be hardcoded in
    `ai_model_registry.py` and configured through env vars. After this runs, models, endpoints and
    API keys are managed at runtime with the /op_aiprovider and /op_aimodel commands; OPENROUTER_API_KEY
    and CUSTOM_PROVIDERS are only ever read here.

Affected Collections:
    - ai_catalog_provider (created)
    - ai_catalog_model (created)

Impact:
    - High risk: with an empty catalog no AI feature can resolve a model. Forward is idempotent per
      document, so re-running it will not duplicate entries.
    - Keys are copied from the environment into the database, where the /op_ commands can rotate them.

Rollback:
    Drops both collections. The previous code read its models from source, so nothing is lost.
"""

from beanie import free_fall_migration

from sophie_bot.config import CONFIG
from sophie_bot.services.db import get_collection

_OPENROUTER = "openrouter"

_MODELS: list[dict] = [
    {
        "name": "openrouter/auto",
        "provider": _OPENROUTER,
        "roles": [{"mode": "entertainment", "purpose": "chatbot"}],
    },
    {
        "name": "qwencloud/qwen3-vl-flash",
        "provider": "qwencloud",
        "api_name": "qwen3-vl-flash",
        "roles": [
            {"mode": "entertainment", "purpose": "translation"},
            {"mode": "entertainment", "purpose": "filters"},
        ],
    },
    {
        "name": "mistralai/mistral-small-2603",
        "provider": _OPENROUTER,
        "roles": [
            {"mode": "moderation", "purpose": "chatbot"},
            {"mode": "moderation", "purpose": "translation"},
            {"mode": None, "purpose": "moderation_reason"},
        ],
    },
    {
        "name": "openai/gpt-5-nano",
        "provider": _OPENROUTER,
        "roles": [
            {"mode": "moderation", "purpose": "filters"},
            {"mode": "support", "purpose": "filters"},
        ],
    },
    {
        "name": "openai/gpt-5.4-mini",
        "provider": _OPENROUTER,
        "roles": [{"mode": "support", "purpose": "chatbot"}],
    },
    {
        "name": "google/gemini-3.1-flash-lite-preview",
        "provider": _OPENROUTER,
        "roles": [{"mode": "support", "purpose": "translation"}],
    },
    {
        "name": "openai/gpt-5.5",
        "provider": _OPENROUTER,
        "extra_params": {"openrouter_reasoning": {"effort": "low"}},
        "roles": [{"mode": None, "purpose": "summary"}],
    },
]


def _providers() -> list[dict]:
    providers = [
        {
            "name": _OPENROUTER,
            "kind": "openrouter",
            "base_url": None,
            "api_key": CONFIG.openrouter_api_key or "",
            "enabled": True,
        }
    ]
    providers.extend(
        {
            "name": custom.name,
            "kind": "openai_compatible",
            "base_url": custom.base_url,
            "api_key": custom.api_key,
            "enabled": True,
        }
        for custom in CONFIG.custom_providers
    )
    return providers


class Forward:
    """Insert the providers and models the code used to hardcode."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        providers = get_collection("ai_catalog_provider")
        for provider in _providers():
            await providers.update_one(
                {"name": provider["name"]}, {"$setOnInsert": provider}, upsert=True, session=session
            )

        models = get_collection("ai_catalog_model")
        for model in _MODELS:
            document = {"supports_reasoning": True, "enabled": True, "api_name": None, "extra_params": None, **model}
            await models.update_one(
                {"name": document["name"]}, {"$setOnInsert": document}, upsert=True, session=session
            )

        print(f"Seeded {len(_providers())} AI providers and {len(_MODELS)} AI models")


class Backward:
    """Drop the catalog; the previous code carried these definitions in source."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        await get_collection("ai_catalog_model").drop(session=session)
        await get_collection("ai_catalog_provider").drop(session=session)
