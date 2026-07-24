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
    - Backward is a no-op; see below.

Rollback:
    Not possible. Forward folds "zai" into "auto", which is also
    AIProviderModel.provider's default, so after it runs the chats it touched are
    indistinguishable from the many chats that were already on "auto". Nothing
    records the pre-migration set, so a Backward cannot identify it.

    The previous Backward reverted *every* chat on "auto" to "zai", i.e. the whole
    default population rather than the handful Forward moved -- and to a provider
    whose models this very migration removed (no `zai`/GLM handling remains in the
    codebase). Reverting nothing is strictly better than corrupting every chat's
    provider, so Backward is an explicit no-op.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection

OLD_PROVIDER = "zai"
NEW_PROVIDER = "auto"


class Forward:
    """Migrate zai provider entries to auto."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        collection = get_collection("ai_provider")
        result = await collection.update_many(
            {"provider": OLD_PROVIDER},
            {"$set": {"provider": NEW_PROVIDER}},
            session=session,
        )
        print(f"Migrated {result.modified_count} chats from '{OLD_PROVIDER}' to '{NEW_PROVIDER}'")


class Backward:
    """No rollback: chats moved to "auto" are indistinguishable from those already on it."""

    @free_fall_migration(document_models=[])
    async def noop(self, session) -> None:
        del session
