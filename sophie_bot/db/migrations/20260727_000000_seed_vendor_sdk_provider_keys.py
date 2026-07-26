"""Migration: seed_vendor_sdk_provider_keys

Description:
    Copies MISTRAL_API_KEY and OPENAI_API_KEY out of the environment into the AI provider catalog,
    as rows named `mistral` and `openai` with kind `moderation`. After this runs, both keys are
    managed at runtime with /op_aiprovider and the env vars are never read again.

    TODO: delete this migration, along with `mistral_api_key` and `openai_api_key` in
    `sophie_bot/config.py` and their entries in `deploy/`, once every deployment has run it.
    It only exists so an existing instance keeps working without an operator re-entering keys.

Affected Collections:
    - ai_catalog_provider (two rows added)

Impact:
    - Medium risk: without these rows the AI moderator and voice/video transcription have no key
      and every request fails with a 401. Forward is idempotent per row.
    - An instance that never set the env vars gets rows with an empty key, which is the same state
      it was already in; the operator fills them in with /op_aiprovider.

Rollback:
    Removes both rows. The keys remain in the environment, but no code reads them any more.
"""

from beanie import free_fall_migration

from sophie_bot.config import CONFIG
from sophie_bot.services.db import get_collection

_PROVIDER_NAMES = ("mistral", "openai")


def _providers() -> list[dict]:
    keys = {"mistral": CONFIG.mistral_api_key, "openai": CONFIG.openai_api_key}
    return [
        {
            "name": name,
            "kind": "moderation",
            "base_url": None,
            "api_key": keys[name] or "",
            "enabled": True,
        }
        for name in _PROVIDER_NAMES
    ]


class Forward:
    """Move the vendor SDK keys from the environment into the catalog."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        providers = get_collection("ai_catalog_provider")
        for provider in _providers():
            await providers.update_one(
                {"name": provider["name"]}, {"$setOnInsert": provider}, upsert=True, session=session
            )

        seeded = [provider["name"] for provider in _providers() if provider["api_key"]]
        print(f"Seeded {len(_PROVIDER_NAMES)} vendor SDK providers, {len(seeded)} with a key from the environment")


class Backward:
    """Remove the rows; the environment still holds the keys."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        await get_collection("ai_catalog_provider").delete_many(
            {"name": {"$in": list(_PROVIDER_NAMES)}}, session=session
        )
