"""Migration: convert_filters_legacy_actions

Description:
    Converts legacy FiltersModel action (string + root-level config) to modern
    actions (dict keyed by modern action name).

Affected Collections:
    - filters

Impact:
    - Medium risk: Migrates stored filter documents to version 2 format.
    - Required before removing legacy filter action runtime support.
"""

from __future__ import annotations

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.utils.logger import log

LEGACY_TO_MODERN: dict[str, str] = {
    "reply_message": "reply",
    "get_note": "send_note",
    "send_rules": "send_rules",
    "delete_message": "delmsg",
    "ai_text": "ai_text",
}

ROOT_CONFIG_FIELDS = frozenset({"reply_text", "note_name", "prompt"})


def build_legacy_filter_migration_update(document: dict[str, Any]) -> dict[str, Any] | None:
    """Build a MongoDB update for one legacy filter document, or None if no migration needed."""
    legacy_action = document.get("action")
    if not legacy_action:
        return None

    if document.get("actions"):
        return None

    modern_action_name = LEGACY_TO_MODERN.get(str(legacy_action))
    if modern_action_name is None:
        log.warning(
            "Skipping filter with unknown legacy action",
            doc_id=document.get("_id"),
            action=legacy_action,
        )
        return None

    action_data: dict[str, Any] = {}
    if legacy_action == "reply_message":
        reply_text = document.get("reply_text")
        if isinstance(reply_text, dict):
            action_data = reply_text
    elif legacy_action == "get_note":
        note_name = document.get("note_name")
        if isinstance(note_name, str):
            action_data = {"notename": note_name}
    elif legacy_action == "ai_text":
        prompt = document.get("prompt")
        if isinstance(prompt, str):
            action_data = {"prompt": prompt}

    unset_fields = {field: "" for field in ROOT_CONFIG_FIELDS if field in document}

    update: dict[str, Any] = {
        "$set": {
            "actions": {modern_action_name: action_data},
            "action": None,
            "version": 2,
        },
    }
    if unset_fields:
        update["$unset"] = unset_fields

    return update


class Forward:
    """Convert legacy filter actions to modern actions dict."""

    @free_fall_migration(document_models=[FiltersModel])
    async def migrate(self, session):
        collection = FiltersModel.get_pymongo_collection()
        async for document in collection.find({"action": {"$ne": None}}):
            update = build_legacy_filter_migration_update(document)
            if update is None:
                continue
            await collection.update_one({"_id": document["_id"]}, update, session=session)


class Backward:
    """Restore legacy action field from a single modern action (best-effort rollback)."""

    MODERN_TO_LEGACY: dict[str, str] = {modern: legacy for legacy, modern in LEGACY_TO_MODERN.items()}

    @free_fall_migration(document_models=[FiltersModel])
    async def rollback(self, session):
        collection = FiltersModel.get_pymongo_collection()
        async for document in collection.find({"action": None, "actions": {"$exists": True, "$ne": {}}}):
            actions = document.get("actions") or {}
            if len(actions) != 1:
                continue

            modern_name, action_data = next(iter(actions.items()))
            legacy_name = self.MODERN_TO_LEGACY.get(modern_name)
            if legacy_name is None:
                continue

            set_fields: dict[str, Any] = {"action": legacy_name, "actions": {}, "version": 1}
            if legacy_name == "reply_message" and isinstance(action_data, dict):
                set_fields["reply_text"] = action_data
            elif legacy_name == "get_note" and isinstance(action_data, dict):
                notename = action_data.get("notename")
                if isinstance(notename, str):
                    set_fields["note_name"] = notename
            elif legacy_name == "ai_text" and isinstance(action_data, dict):
                prompt = action_data.get("prompt")
                if isinstance(prompt, str):
                    set_fields["prompt"] = prompt

            await collection.update_one({"_id": document["_id"]}, {"$set": set_fields}, session=session)
