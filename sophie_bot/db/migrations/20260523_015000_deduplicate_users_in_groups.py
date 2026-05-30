"""Migration: deduplicate_users_in_groups

Description:
    Removes duplicate `users_in_groups` records and drops old indexes so Banie
    can create the single unique `$id` index.

Affected Collections:
    - users_in_groups

Impact:
    - Keeps one newest membership record per user/group pair.
    - Rollback is intentionally a no-op because deleted duplicates cannot be
      reconstructed safely.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pymongo.errors
from beanie import free_fall_migration

from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.utils.logger import log


def _reference_id(reference: Any) -> Any:
    if hasattr(reference, "id"):
        return reference.id
    if isinstance(reference, dict):
        return reference.get("$id") or reference.get("id")
    return reference


def _deduplicate_sort_key(document: dict[str, Any]) -> tuple[datetime, datetime, Any]:
    return (
        document.get("last_saw") or datetime.min,
        document.get("first_saw") or datetime.min,
        document.get("_id"),
    )


class Forward:
    """Deduplicate users_in_groups records."""

    @free_fall_migration(document_models=[UserInGroupModel])
    async def deduplicate(self, session) -> None:
        collection = UserInGroupModel.get_pymongo_collection()

        # Drop old indexes so Banie can create the single unique index.
        # MongoDB won't alter an existing index from non-unique to unique.
        # The user.id/group.id index is removed entirely — Link fields store
        # as DBRef ($id), so user.id is always null and the index is useless.
        for old_index_name in (
            "user.$id_1_group.$id_1",
            "user.id_1_group.id_1",
            "user_group_ref_key",
        ):
            try:
                await collection.drop_index(old_index_name)
                log.info("Dropped old index", index_name=old_index_name)
            except pymongo.errors.OperationFailure:
                # Index doesn't exist — nothing to drop.
                pass

        grouped_documents: dict[tuple[Any, Any], list[dict[str, Any]]] = {}

        async for document in collection.find(session=session):
            user_iid = _reference_id(document.get("user"))
            group_iid = _reference_id(document.get("group"))
            if user_iid is None or group_iid is None:
                continue
            grouped_documents.setdefault((user_iid, group_iid), []).append(document)

        deleted_count = 0
        duplicate_group_count = 0

        for documents in grouped_documents.values():
            if len(documents) <= 1:
                continue

            duplicate_group_count += 1
            documents.sort(key=_deduplicate_sort_key, reverse=True)
            duplicate_ids = [document["_id"] for document in documents[1:]]
            delete_result = await collection.delete_many({"_id": {"$in": duplicate_ids}}, session=session)
            deleted_count += delete_result.deleted_count

        if deleted_count > 0:
            log.info(
                "Deduplicated users_in_groups records",
                deleted_count=deleted_count,
                duplicate_group_count=duplicate_group_count,
            )


class Backward:
    """No rollback: removed duplicate records cannot be reconstructed safely."""

    @free_fall_migration(document_models=[UserInGroupModel])
    async def noop(self, session) -> None:
        del session
