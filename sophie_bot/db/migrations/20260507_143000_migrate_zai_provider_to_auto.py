"""Migration: migrate_zai_provider_to_auto

Description:
    Removes the Z.AI (zai) provider and all GLM models. Transfers chats
    that had provider="zai" to provider="auto" so they fall back to the
    default model selection without breaking.

Affected Collections:
    - ai_provider

Impact:
    - Chats using zai provider will be migrated to auto
    - No data loss — auto provider picks best default model
"""

from beanie import free_fall_migration

from sophie_bot.db.models.ai.ai_provider import AIProviderModel

OLD_PROVIDER = "zai"
NEW_PROVIDER = "auto"


class Forward:
    """Migrate zai provider entries to auto."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def migrate(self, session):
        collection = AIProviderModel.get_pymongo_collection()
        result = await collection.update_many(
            {"provider": OLD_PROVIDER},
            {"$set": {"provider": NEW_PROVIDER}},
            session=session,
        )
        print(f"Migrated {result.modified_count} chats from '{OLD_PROVIDER}' to '{NEW_PROVIDER}'")


class Backward:
    """Revert auto provider entries (originally zai) back to zai."""

    @free_fall_migration(document_models=[AIProviderModel])
    async def rollback(self, session):
        collection = AIProviderModel.get_pymongo_collection()
        await collection.update_many(
            {"provider": NEW_PROVIDER},
            {"$set": {"provider": OLD_PROVIDER}},
            session=session,
        )
