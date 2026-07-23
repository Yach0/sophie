"""Migration: add_deep_help_model

Description:
    Adds the model the experimental deep_help sub-agent uses to the AI catalog, so it can be
    swapped with /op_aimodel like every other model instead of living in a feature flag default.

Affected Collections:
    - ai_catalog_model

Impact:
    - Low risk: one additive catalog entry. deep_help is off by default, and without this entry it
      reports that it could not answer rather than failing.

Rollback:
    Removes the deep_help role, leaving the model itself in place in case it serves another purpose.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection

_MODEL_NAME = "openai/gpt-5.6-luna"
_ROLE = {"mode": None, "purpose": "deep_help"}


class Forward:
    """Give the deep_help purpose a model, without disturbing one already in the catalog."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        models = get_collection("ai_catalog_model")
        await models.update_one(
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
    """Drop only the role: the model may have been given other purposes since."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        await get_collection("ai_catalog_model").update_one(
            {"name": _MODEL_NAME}, {"$pull": {"roles": _ROLE}}, session=session
        )
