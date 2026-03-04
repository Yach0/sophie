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
"""

from beanie import free_fall_migration

from sophie_bot.db.models.warns import WarnSettingsModel


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
    """Restore legacy warn actions from max-warn scope."""

    @free_fall_migration(document_models=[WarnSettingsModel])
    async def rollback(self, session):
        collection = WarnSettingsModel.get_pymongo_collection()
        async for doc in collection.find():
            max_actions = doc.get("on_max_warn_actions") or []
            await collection.update_one(
                {"_id": doc["_id"]},
                {"$set": {"actions": max_actions}},
                session=session,
            )
