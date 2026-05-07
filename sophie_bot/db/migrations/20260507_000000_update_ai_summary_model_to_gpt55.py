"""Migration: update_ai_summary_model_to_gpt55

Description:
    Updates provider documents that still use the previous default summary
    model to the new GPT-5.5 summary model.

Affected Collections:
    - ai_provider

Impact:
    - Low risk: only documents using the old default summary model change
"""

from beanie import free_fall_migration

from sophie_bot.db.models.ai.ai_provider import AIProviderModel

OLD_SUMMARY_MODEL_NAME = "openai/gpt-5.4"
NEW_SUMMARY_MODEL_NAME = "openai/gpt-5.5"


class Forward:
    """Move provider documents from the old default summary model."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def migrate(self, session):
        collection = AIProviderModel.get_pymongo_collection()
        await collection.update_many(
            {"summary_model": OLD_SUMMARY_MODEL_NAME},
            {"$set": {"summary_model": NEW_SUMMARY_MODEL_NAME}},
            session=session,
        )


class Backward:
    """Restore provider documents using the new default summary model."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def rollback(self, session):
        collection = AIProviderModel.get_pymongo_collection()
        await collection.update_many(
            {"summary_model": NEW_SUMMARY_MODEL_NAME},
            {"$set": {"summary_model": OLD_SUMMARY_MODEL_NAME}},
            session=session,
        )
