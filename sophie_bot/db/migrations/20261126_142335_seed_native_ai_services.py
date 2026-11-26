"""Seed native transcription and moderation endpoints in the operator model matrix.

Low risk: adds four models and missing empty-key vendor providers without changing existing
catalog rows, moderation enablement, thresholds or detection levels. The global legacy
moderation-provider choice seeds the mode roles. Transcription is a shared support-row service.
Rollback removes only unchanged rows created here; operator edits and keys are retained.
Run migration and rollback with catalog consumers and writers stopped, then restart them.
Startup loads the catalog after migrations commit; no Redis invalidation is needed here.
"""

from __future__ import annotations

from beanie import free_fall_migration
from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from sophie_bot.services.migrations import MigrationResources

_BACKUP = "ai_native_catalog_seed_backup"
_MODELS = {
    "mistral": {"transcription": "voxtral-mini-latest", "moderation": "mistral-moderation-latest"},
    "openai": {"transcription": "gpt-transcribe", "moderation": "omni-moderation-latest"},
}


async def _seed(
    collection: AsyncCollection,
    backup: AsyncCollection,
    document: dict[str, object],
    session: AsyncClientSession | None,
) -> None:
    backup_id = f"{collection.name}/{document['name']}"
    snapshot = await backup.find_one({"_id": backup_id}, session=session)
    if snapshot is None:
        if await collection.find_one({"name": document["name"]}, session=session) is not None:
            return
        await backup.update_one(
            {"_id": backup_id},
            {"$setOnInsert": {"collection": collection.name, "document": {"_id": ObjectId(), **document}}},
            upsert=True,
            session=session,
        )
        snapshot = await backup.find_one({"_id": backup_id}, session=session)
    assert snapshot is not None
    await collection.update_one(
        {"name": document["name"]}, {"$setOnInsert": snapshot["document"]}, upsert=True, session=session
    )


class Forward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        database = resources.database.database
        backup = database[_BACKUP]
        providers = database["ai_catalog_provider"]
        models = database["ai_catalog_model"]
        legacy = await database["feature_flag_overrides"].find_one(
            {"feature": "ai_moderation_provider", "chat_tid": None}, session=session
        )
        selected_provider = "openai" if legacy and legacy.get("value") == "openai" else "mistral"
        for provider_name, purposes in _MODELS.items():
            await _seed(
                providers,
                backup,
                {
                    "name": provider_name,
                    "kind": "moderation",
                    "api_key": "",
                    "enabled": True,
                },
                session,
            )
            provider = await providers.find_one({"name": provider_name}, session=session)
            assert provider is not None
            for purpose, api_name in purposes.items():
                roles: list[dict[str, object]] = []
                if purpose == "transcription" and provider_name == "mistral":
                    roles = [{"mode": "support", "purpose": purpose}]
                elif purpose == "moderation":
                    roles = [
                        {
                            "mode": mode,
                            "purpose": purpose,
                            **({"priority": 10} if provider_name != selected_provider else {}),
                        }
                        for mode in ("support", "moderation", "entertainment")
                    ]
                await _seed(
                    models,
                    backup,
                    {
                        "name": f"{provider_name}/{api_name}",
                        "provider": provider_name,
                        "api_name": api_name,
                        "supports_reasoning": False,
                        "supports_images": False,
                        "enabled": provider.get("enabled", True),
                        "roles": roles,
                    },
                    session,
                )


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        database = resources.database.database
        backup = database[_BACKUP]
        # Models go first so an unchanged provider is removed only if it is still unused.
        for collection_name in ("ai_catalog_model", "ai_catalog_provider"):
            async for snapshot in backup.find({"collection": collection_name}, session=session):
                document = snapshot["document"]
                if collection_name == "ai_catalog_provider" and await database["ai_catalog_model"].count_documents(
                    {"provider": document["name"]}, session=session
                ):
                    continue
                current = await database[collection_name].find_one({"_id": document["_id"]}, session=session)
                if current == document:
                    await database[collection_name].delete_one({"_id": document["_id"]}, session=session)
        await backup.delete_many({}, session=session)
