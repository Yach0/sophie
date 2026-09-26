"""Migration: seed_ai_catalog

Description:
    Seeds the AI provider and model catalog from what used to be hardcoded in
    `ai_model_registry.py`. Providers start without a key; credentials are managed in
    the catalog through /op_aiprovider after this migration.

Affected Collections:
    - ai_catalog_provider (created)
    - ai_catalog_model (created)

Impact:
    - High risk: with an empty catalog no AI feature can resolve a model. Forward is idempotent per
      document, so re-running it will not duplicate entries.
    - Provider keys are configured in the catalog after this migration.

Rollback:
    Drops both collections. The previous code read its models from source, so nothing is lost.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection
from sophie_bot.services.migrations import MigrationResources

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


_PROVIDERS: tuple[dict, ...] = (
    {
        "name": _OPENROUTER,
        "kind": "openrouter",
        "base_url": None,
        "api_key": "",
        "enabled": True,
    },
)


class Forward:
    """Insert the providers and models the code used to hardcode."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session, *, resources: MigrationResources) -> None:
        providers = get_collection(resources.database.database, "ai_catalog_provider")
        for provider in _PROVIDERS:
            await providers.update_one(
                {"name": provider["name"]}, {"$setOnInsert": provider}, upsert=True, session=session
            )

        models = get_collection(resources.database.database, "ai_catalog_model")
        for model in _MODELS:
            document = {"supports_reasoning": True, "enabled": True, "api_name": None, "extra_params": None, **model}
            await models.update_one(
                {"name": document["name"]}, {"$setOnInsert": document}, upsert=True, session=session
            )

        print(f"Seeded {len(_PROVIDERS)} AI providers and {len(_MODELS)} AI models")


class Backward:
    """Drop the catalog; the previous code carried these definitions in source."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session, *, resources: MigrationResources) -> None:
        await get_collection(resources.database.database, "ai_catalog_model").drop(session=session)
        await get_collection(resources.database.database, "ai_catalog_provider").drop(session=session)
