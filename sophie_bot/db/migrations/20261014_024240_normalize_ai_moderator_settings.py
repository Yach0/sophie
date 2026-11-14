"""Normalize legacy ai_moderator defaults and deduplicate settings before index sync.

Affected collections: ai_moderator, ai_moderator_upgrade_backup.
Medium risk, small settings collection. Run with bot/API writers stopped. Startup runs
this repair before creating the unique chat index; installations disabling automatic
migrations must run it manually before starting this version.

Legacy True is ambiguous: both category edits and explicit REST enables wrote it,
without provenance. It follows the mode default after upgrade, preventing accidental
Entertainment opt-in. False remains an explicit override. For duplicates, retain the
newest ObjectId's category settings and preserve False from any duplicate, since
it is the only unambiguous legacy enabled override.
Archive every original before changing/deleting it, for retry safety and rollback.
Backward drops the unique index and restores archived snapshots, including duplicates;
post-upgrade edits to archived records are replaced. New, unarchived records with
null/missing enabled use the legacy True default. Stop writers for rollback too.
"""

from __future__ import annotations

from beanie import free_fall_migration
from bson import DBRef, ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.services.migrations import MigrationResources


class Forward:
    """Archive legacy settings, normalize implicit enables, and keep one record per chat."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_moderator"]
        backup = resources.database.database["ai_moderator_upgrade_backup"]
        survivors: dict[DBRef, ObjectId] = {}
        async for document in collection.find({}, sort=[("_id", -1)], session=session):
            await backup.update_one(
                {"_id": document["_id"]},
                {"$setOnInsert": {"original": document, "applied": False}},
                upsert=True,
                session=session,
            )
            archived = await backup.find_one({"_id": document["_id"]}, session=session)
            assert archived is not None
            if document["chat"] in survivors:
                if document.get("enabled") is False:
                    await collection.update_one(
                        {"_id": survivors[document["chat"]]}, {"$set": {"enabled": False}}, session=session
                    )
                await collection.delete_one({"_id": document["_id"]}, session=session)
                continue
            survivors[document["chat"]] = document["_id"]
            if not archived["applied"] and document.get("enabled", True) is True:
                await collection.update_one({"_id": document["_id"]}, {"$unset": {"enabled": ""}}, session=session)
            await backup.update_one({"_id": document["_id"]}, {"$set": {"applied": True}}, session=session)


class Backward:
    """Restore snapshots and normalize new settings to the legacy boolean default."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_moderator"]
        backup = resources.database.database["ai_moderator_upgrade_backup"]
        # Index DDL cannot run in the data transaction. Remove it before restoring duplicates.
        if "moderator_chat_unique" in await collection.index_information():
            await collection.drop_index("moderator_chat_unique")
        # MongoDB's null match includes missing fields. Restore snapshots afterwards
        # so archived values, including null/missing enabled, remain unchanged.
        await collection.update_many({"enabled": None}, {"$set": {"enabled": True}}, session=session)
        async for document in backup.find({}, session=session):
            await collection.replace_one({"_id": document["_id"]}, document["original"], upsert=True, session=session)
        await backup.delete_many({}, session=session)
