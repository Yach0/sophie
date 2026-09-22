"""Migration: seed_vendor_sdk_provider_keys

Description:
    Creates the `mistral` and `openai` catalog providers for vendor SDK calls.
    The operator configures their API keys in the catalog with /op_aiprovider.

Affected Collections:
    - ai_catalog_provider (two rows added)

Impact:
    - Without configured keys, moderation and transcription requests cannot authenticate.
    - The migration is idempotent and does not overwrite existing provider credentials.

Rollback:
    Removes both provider rows.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection
from sophie_bot.services.migrations import MigrationResources

_PROVIDER_NAMES = ("mistral", "openai")


def _providers() -> list[dict]:
    return [
        {
            "name": name,
            "kind": "moderation",
            "base_url": None,
            "api_key": "",
            "enabled": True,
        }
        for name in _PROVIDER_NAMES
    ]


class Forward:
    """Create catalog rows for vendor SDK providers."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session, *, resources: MigrationResources) -> None:
        providers = get_collection(resources.database.database, "ai_catalog_provider")
        for provider in _providers():
            await providers.update_one(
                {"name": provider["name"]}, {"$setOnInsert": provider}, upsert=True, session=session
            )

        print(f"Seeded {len(_PROVIDER_NAMES)} vendor SDK providers")


class Backward:
    """Remove the vendor SDK provider rows."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session, *, resources: MigrationResources) -> None:
        await get_collection(resources.database.database, "ai_catalog_provider").delete_many(
            {"name": {"$in": list(_PROVIDER_NAMES)}}, session=session
        )
