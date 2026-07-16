"""Migration: update_ai_summary_model_to_gpt55

Description:
    Updates provider documents that still use the previous default summary
    model to the new GPT-5.5 summary model.

Affected Collections:
    - ai_provider

Impact:
    - Low risk: only documents using the old default summary model change
    - Backward is a no-op; see below.

Rollback:
    Not possible. Forward folds "openai/gpt-5.4" into "openai/gpt-5.5", which is also
    constants.DEFAULT_AI_SUMMARY_MODEL and therefore AIProviderModel.summary_model's
    default. After it runs, the documents it touched are indistinguishable from every
    document that already carried the new default -- documents that took it as their
    default, and documents whose owners chose it deliberately. Nothing records the
    pre-migration set, so a Backward cannot identify it.

    The previous Backward reverted *every* document on "openai/gpt-5.5" to
    "openai/gpt-5.4", i.e. the whole default population rather than the subset Forward
    moved. Reverting nothing is strictly better than downgrading every chat's summary
    model, so Backward is an explicit no-op.
"""

from beanie import free_fall_migration

from sophie_bot.db.models.ai.ai_provider import AIProviderModel

OLD_SUMMARY_MODEL_NAME = "openai/gpt-5.4"
NEW_SUMMARY_MODEL_NAME = "openai/gpt-5.5"


class Forward:
    """Move provider documents from the old default summary model."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def migrate(self, session) -> None:
        collection = AIProviderModel.get_pymongo_collection()
        await collection.update_many(
            {"summary_model": OLD_SUMMARY_MODEL_NAME},
            {"$set": {"summary_model": NEW_SUMMARY_MODEL_NAME}},
            session=session,
        )


class Backward:
    """No rollback: documents moved to the new default are indistinguishable from those already on it."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def noop(self, session) -> None:
        del session
