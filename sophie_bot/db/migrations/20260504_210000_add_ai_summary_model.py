"""Migration: add_ai_summary_model

Description:
    Adds the dedicated `summary_model` field to AI provider settings and
    backfills existing documents with the summary model used by daily chat
    summarization.

Affected Collections:
    - ai_provider

Impact:
    - Low risk: additive field with deterministic backfill
    - Existing documents gain `summary_model = "openai/gpt-5.4"`
"""

from beanie import free_fall_migration

from sophie_bot.db.models.ai.ai_provider import AIProviderModel

SUMMARY_MODEL_NAME = "openai/gpt-5.4"


class Forward:
    """Backfill the dedicated summary model on provider documents."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def migrate(self, session):
        collection = AIProviderModel.get_pymongo_collection()
        await collection.update_many(
            {"summary_model": {"$exists": False}},
            {"$set": {"summary_model": SUMMARY_MODEL_NAME}},
            session=session,
        )


class Backward:
    """Remove the dedicated summary model field."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def rollback(self, session):
        collection = AIProviderModel.get_pymongo_collection()
        await collection.update_many(
            {"summary_model": SUMMARY_MODEL_NAME},
            {"$unset": {"summary_model": ""}},
            session=session,
        )
