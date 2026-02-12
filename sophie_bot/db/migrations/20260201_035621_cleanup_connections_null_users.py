"""Migration: cleanup_connections_null_users

Description:
    Removes connection records where user is null to prevent unique index violations.
    This must run before the convert_connections_to_links migration to ensure
    the unique index on the user field can be created successfully.

Affected Collections:
    - connections

Impact:
    - Low risk: Removes orphaned records that are already invalid
    - Required for: Unique index creation on user field
"""

from __future__ import annotations

from beanie import free_fall_migration
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.utils.logger import log


class Forward:
    """Remove connection records with null user field."""

    @free_fall_migration(document_models=[ChatConnectionModel])
    async def cleanup_null_users(self, session):
        collection = ChatConnectionModel.get_pymongo_collection()

        # Find and delete all documents where user is null or missing
        cursor = collection.find({"$or": [{"user": None}, {"user": {"$exists": False}}]})
        deleted_count = 0

        async for doc in cursor:
            doc_id = doc.get("_id")
            log.warning(
                "Deleting connection record with null/missing user",
                doc_id=str(doc_id),
                user_id=doc.get("user_id"),
                chat_id=doc.get("chat_id"),
            )
            await collection.delete_one({"_id": doc_id}, session=session)
            deleted_count += 1

        if deleted_count > 0:
            log.info(
                "Cleanup completed",
                deleted_records=deleted_count,
                collection="connections",
            )


class Backward:
    """No rollback needed - data was invalid and deleted."""

    @free_fall_migration(document_models=[ChatConnectionModel])
    async def noop(self, session):
        # Cannot restore deleted invalid records
        pass
