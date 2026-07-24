"""Migration: seed_research_role

Description:
    Gives the ``research`` purpose a catalog model, so it is chosen per chat mode like every other
    purpose instead of only from the ai_research_model feature-flag default. The default becomes
    empty in the same change, making the catalog the source of truth with the flag as an override.

Affected Collections:
    - ai_catalog_model

Impact:
    - Low risk: adds one any-mode research role to the model that used to be the flag default. No-op
      if the role is already present.

Rollback:
    Removes the research role again.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection

# The model the ai_research_model flag used to default to.
_MODEL_NAME = "openai/gpt-5.5"
_ROLE = {"mode": None, "purpose": "research"}


class Forward:
    """Give research an any-mode role on the model that was its flag default."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        await get_collection("ai_catalog_model").update_one(
            {"name": _MODEL_NAME},
            {
                "$setOnInsert": {
                    "name": _MODEL_NAME,
                    "provider": "openrouter",
                    "api_name": None,
                    "supports_reasoning": True,
                    "extra_params": None,
                    "enabled": True,
                },
                "$addToSet": {"roles": _ROLE},
            },
            upsert=True,
            session=session,
        )


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        await get_collection("ai_catalog_model").update_one(
            {"name": _MODEL_NAME}, {"$pull": {"roles": _ROLE}}, session=session
        )
