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
    - Backward is a no-op; see below.

Rollback:
    Not possible. Forward backfills documents that lack `summary_model`, and afterwards a
    backfilled document is indistinguishable from one that already held "openai/gpt-5.4"
    because its owner chose it -- the model still offers gpt-5.4. Nothing records which
    documents were missing the field.

    The previous Backward `$unset` every document whose `summary_model` equalled
    "openai/gpt-5.4", which silently erased those deliberate choices, dropping them back to
    `AIProviderModel.summary_model`'s default. Forward never touched them.

    Leaving the field populated is harmless for pre-migration code, which ignores it, so
    Backward does nothing.
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
    """No rollback: backfilled documents are indistinguishable from a deliberate gpt-5.4 choice."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def noop(self, session) -> None:
        del session
