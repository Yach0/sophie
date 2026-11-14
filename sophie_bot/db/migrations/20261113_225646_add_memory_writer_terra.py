"""Register OpenRouter Terra for the independent memory writer.

Affected collections: ai_catalog_model, ai_catalog_provider, ai_memory_writer_catalog_backup.
Low risk, one model. Existing model settings and other roles are preserved. Stop catalog
writers during migration and rollback. The backup makes retries safe. Rollback restores
an existing model's original snapshot, or removes the model created by this migration;
post-migration changes to that model are therefore lost on rollback. No provider keys or
memory rollout flags are changed. An empty OpenRouter provider is created only when missing;
rollback removes that placeholder only if it is still unused and has no key.
"""

from __future__ import annotations

from beanie import free_fall_migration
from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.modules.ai.utils.ai_catalog import bump_version
from sophie_bot.services.migrations import MigrationResources

MODEL_NAME = "gpt-6.1-terra"
ROLE = {"mode": "entertainment", "purpose": "memory_writer"}


class Forward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_catalog_model"]
        backup = resources.database.database["ai_memory_writer_catalog_backup"]
        providers = resources.database.database["ai_catalog_provider"]
        provider = await providers.find_one({"name": "openrouter"}, session=session)
        if provider is not None and provider.get("kind") != "openrouter":
            raise ValueError("The openrouter catalog provider uses another provider kind")
        original = await collection.find_one({"name": MODEL_NAME}, session=session)
        if original is not None and original.get("provider") != "openrouter":
            raise ValueError("The existing gpt-6.1-terra catalog model uses another provider")
        await backup.update_one(
            {"_id": MODEL_NAME},
            {"$setOnInsert": {"original": original, "created_id": ObjectId(), "provider_created_id": ObjectId()}},
            upsert=True,
            session=session,
        )
        snapshot = await backup.find_one({"_id": MODEL_NAME}, session=session)
        assert snapshot is not None
        await providers.update_one(
            {"name": "openrouter"},
            {
                "$setOnInsert": {
                    "_id": snapshot["provider_created_id"],
                    "name": "openrouter",
                    "kind": "openrouter",
                    "api_key": "",
                    "enabled": True,
                }
            },
            upsert=True,
            session=session,
        )
        await collection.update_one(
            {"name": MODEL_NAME},
            {
                "$setOnInsert": {
                    "_id": snapshot["created_id"],
                    "name": MODEL_NAME,
                    "provider": "openrouter",
                    "supports_reasoning": True,
                    "supports_images": False,
                    "enabled": provider.get("enabled", True) if provider is not None else True,
                }
            },
            upsert=True,
            session=session,
        )
        model = await collection.find_one({"name": MODEL_NAME}, session=session)
        assert model is not None
        if not any(
            role.get("mode") == ROLE["mode"] and role.get("purpose") == ROLE["purpose"]
            for role in model.get("roles", [])
        ):
            await collection.update_one({"_id": model["_id"]}, {"$push": {"roles": ROLE}}, session=session)
        await bump_version(redis=resources.redis)


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_catalog_model"]
        backup = resources.database.database["ai_memory_writer_catalog_backup"]
        snapshot = await backup.find_one({"_id": MODEL_NAME}, session=session)
        if snapshot is None:
            return
        if snapshot["original"] is None:
            await collection.delete_one({"_id": snapshot["created_id"]}, session=session)
        else:
            await collection.replace_one(
                {"_id": snapshot["original"]["_id"]},
                snapshot["original"],
                upsert=True,
                session=session,
            )
        if await collection.count_documents({"provider": "openrouter"}, session=session) == 0:
            await resources.database.database["ai_catalog_provider"].delete_one(
                {"_id": snapshot["provider_created_id"], "api_key": ""},
                session=session,
            )
        await backup.delete_one({"_id": MODEL_NAME}, session=session)
        await bump_version(redis=resources.redis)
