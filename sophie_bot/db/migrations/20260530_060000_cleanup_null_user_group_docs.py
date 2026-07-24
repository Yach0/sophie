"""Migration: cleanup_null_user_group_docs

Description:
    Deletes users_in_groups documents where user or group reference is null
    or missing. These documents were skipped by the earlier dedup migration
    and block unique index creation because multiple {null, null} rows
    violate the unique constraint.

Affected Collections:
    - users_in_groups

Impact:
    - Removes orphaned records with no valid user/group reference.
    - These records are unreachable by any query and serve no purpose.
"""

from __future__ import annotations

from beanie import free_fall_migration

from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.utils.logger import log


class Forward:
    """Delete users_in_groups documents with null or missing references."""

    @free_fall_migration(document_models=[UserInGroupModel])
    async def cleanup_null_refs(self, session) -> None:
        collection = UserInGroupModel.get_pymongo_collection()

        # Drop old indexes that conflict with current model definitions.
        for index_name in (
            "user.$id_1_group.$id_1",
            "user.id_1_group.id_1",
            "user_group_ref_key",
        ):
            try:
                await collection.drop_index(index_name)
                log.info("Dropped old index", index_name=index_name)
            except Exception:  # noqa: BLE001
                log.debug("Old index not present, skipping", index_name=index_name)

        # Delete documents where user or group is null/missing.
        result = await collection.delete_many(
            {"$or": [{"user": None}, {"user": {"$exists": False}}, {"group": None}, {"group": {"$exists": False}}]},
            session=session,
        )
        if result.deleted_count > 0:
            log.info("Deleted users_in_groups with null references", deleted_count=result.deleted_count)


class Backward:
    """No rollback: deleted orphaned records cannot be reconstructed."""

    @free_fall_migration(document_models=[UserInGroupModel])
    async def noop(self, session) -> None:
        del session
