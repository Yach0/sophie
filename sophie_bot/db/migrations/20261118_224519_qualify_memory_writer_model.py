"""Qualify Terra's upstream OpenRouter name without changing its catalog identity.

Low risk: updates one ai_catalog_model row and stores its original api_name in
ai_memory_writer_api_name_backup. Existing custom upstream names are preserved.
Stop catalog writers during migration and rollback. Rollback restores only the
api_name and preserves any subsequent operator change to that field.
"""

from __future__ import annotations

from beanie import free_fall_migration
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.modules.ai.utils.ai_catalog import bump_version
from sophie_bot.services.migrations import MigrationResources

MODEL_NAME = "gpt-6.1-terra"
API_NAME = "openai/gpt-6.1-terra"


class Forward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_catalog_model"]
        model = await collection.find_one({"name": MODEL_NAME, "provider": "openrouter"}, session=session)
        if model is None or model.get("api_name") not in (None, "", MODEL_NAME):
            return
        backup = resources.database.database["ai_memory_writer_api_name_backup"]
        await backup.update_one(
            {"_id": MODEL_NAME},
            {"$setOnInsert": {"original": {key: model[key] for key in ("_id", "api_name") if key in model}}},
            upsert=True,
            session=session,
        )
        await collection.update_one({"_id": model["_id"]}, {"$set": {"api_name": API_NAME}}, session=session)
        await bump_version(redis=resources.redis)


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        backup = resources.database.database["ai_memory_writer_api_name_backup"]
        snapshot = await backup.find_one({"_id": MODEL_NAME}, session=session)
        if snapshot is None:
            return
        original = snapshot["original"]
        change = (
            {"$set": {"api_name": original["api_name"]}} if "api_name" in original else {"$unset": {"api_name": ""}}
        )
        await resources.database.database["ai_catalog_model"].update_one(
            {"_id": original["_id"], "api_name": API_NAME}, change, session=session
        )
        await backup.delete_one({"_id": MODEL_NAME}, session=session)
        await bump_version(redis=resources.redis)
