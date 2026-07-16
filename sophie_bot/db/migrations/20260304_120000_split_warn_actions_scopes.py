"""Migration: split_warn_actions_scopes

Description:
    Introduces per-scope warn actions:
    - on_each_warn_actions
    - on_max_warn_actions
    Migrates legacy `actions` into `on_max_warn_actions` when needed.

Affected Collections:
    - warn_settings

Impact:
    - Low risk: field-shape evolution with backward-compatible fallback
    - Small collection: one settings document per chat

Rollback:
    Forward is purely additive -- it copies legacy `actions` into `on_max_warn_actions` and
    never modifies `actions` itself. So the faithful inverse is to remove the two fields
    Forward added, from exactly the documents it added them to, and to leave `actions` alone.

    Forward's documents are identifiable by its own postcondition (see
    `is_forward_migrated_warn_settings`), because it preserved the source data in `actions`.

    The previous Backward instead iterated *every* document and unconditionally
    `$set actions = on_max_warn_actions or []`, writing a field Forward never touched. That
    overwrote the legacy `actions` of documents Forward skipped, fabricated a legacy
    `actions` on chats configured after the migration through the current API (which have no
    legacy field at all), and wrote `actions: []` onto documents that had neither field. It
    also left `on_max_warn_actions`/`on_each_warn_actions` in place, so a rollback produced a
    hybrid shape rather than the pre-migration one.
"""

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.warns import WarnSettingsModel


def is_forward_migrated_warn_settings(document: dict[str, Any]) -> bool:
    """Match Forward's postcondition: legacy actions copied verbatim into the max-warn scope.

    Documents configured through the current API carry no legacy `actions`, so they never match
    and keep their scoped actions.
    """
    legacy_actions = document.get("actions")
    if not legacy_actions:
        return False
    return document.get("on_max_warn_actions") == legacy_actions and document.get("on_each_warn_actions") == []


class Forward:
    """Copy legacy warn actions into max-warn scope."""

    @free_fall_migration(document_models=[WarnSettingsModel])
    async def migrate(self, session):
        collection = WarnSettingsModel.get_pymongo_collection()
        async for doc in collection.find():
            if doc.get("on_max_warn_actions"):
                continue

            legacy_actions = doc.get("actions") or []
            if not legacy_actions:
                continue

            await collection.update_one(
                {"_id": doc["_id"]},
                {"$set": {"on_max_warn_actions": legacy_actions, "on_each_warn_actions": []}},
                session=session,
            )


class Backward:
    """Remove the scoped warn actions from the documents Forward added them to."""

    @free_fall_migration(document_models=[WarnSettingsModel])
    async def rollback(self, session) -> None:
        collection = WarnSettingsModel.get_pymongo_collection()
        async for document in collection.find():
            if not is_forward_migrated_warn_settings(document):
                continue
            await collection.update_one(
                {"_id": document["_id"]},
                {"$unset": {"on_max_warn_actions": "", "on_each_warn_actions": ""}},
                session=session,
            )
