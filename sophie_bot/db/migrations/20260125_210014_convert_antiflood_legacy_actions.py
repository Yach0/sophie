"""Migration: convert_antiflood_legacy_actions

Description:
    Converts legacy AntifloodModel action (string) to modern actions (list of FilterActionType).

Affected Collections:
    - antiflood

Impact:
    - Low risk: Normalizes action format.
    - Small collection: Antiflood settings are per-chat.

Rollback:
    The legacy `action` string can only encode a bare mute/kick/ban, so a document is
    convertible back only if it holds exactly what Forward produced: a single known action
    with no `data` payload. Backward is restricted to that shape
    (`is_forward_migrated_antiflood_action`), which is lossless for those documents.

    It previously converted any single legacy-named action regardless of `data`, on every
    document rather than the ones Forward touched. An antiflood configured after the
    migration with, say, a mute duration in `data` was rewritten to `action: "mute"`,
    `actions: []` -- silently discarding the payload the legacy field cannot express.
    Documents carrying `data` are now left in the modern shape instead of being truncated.
"""

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.utils.logger import log

LEGACY_ACTIONS = {"mute", "kick", "ban"}
LEGACY_ACTIONS_TO_MODERN = {"mute": "mute_user", "kick": "kick_user", "ban": "ban_user"}
MODERN_TO_LEGACY_ACTIONS = {modern: legacy for legacy, modern in LEGACY_ACTIONS_TO_MODERN.items()}


def is_forward_migrated_antiflood_action(actions: list[dict[str, Any]]) -> bool:
    """Match Forward's output: one legacy-mappable action carrying no data.

    A `data` payload has no representation in the legacy `action` string, so any action that
    has one must be left alone rather than truncated.
    """
    if len(actions) != 1:
        return False
    action = actions[0]
    return action.get("name") in MODERN_TO_LEGACY_ACTIONS and not action.get("data")


class Forward:
    """Convert legacy action to modern actions list and ensure chat Link exists."""

    @free_fall_migration(document_models=[AntifloodModel])
    async def migrate(self, session):
        collection = AntifloodModel.get_pymongo_collection()
        async for doc in collection.find():
            # Handle chat_id -> chat conversion first
            if "chat" not in doc and "chat_id" in doc:
                chat_id = doc["chat_id"]
                chat = await ChatModel.find_one(ChatModel.tid == chat_id)
                if chat:
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"chat": chat.id}, "$unset": {"chat_id": ""}},
                        session=session,
                    )
                    # Update local doc for subsequent processing
                    doc["chat"] = chat.id
                else:
                    log.warning(
                        "Deleting orphaned antiflood record without corresponding chat",
                        chat_id=chat_id,
                        doc_id=doc["_id"],
                    )
                    await collection.delete_one({"_id": doc["_id"]}, session=session)
                    continue

            if "chat" not in doc:
                log.warning("Skipping antiflood document without chat link", doc_id=doc.get("_id"))
                continue

            if not doc.get("actions") and doc.get("action") in LEGACY_ACTIONS:
                modern_name = LEGACY_ACTIONS_TO_MODERN[str(doc["action"])]
                await collection.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"actions": [{"name": modern_name, "data": {}}], "action": None}},
                    session=session,
                )


class Backward:
    """Convert modern actions back to legacy action and restore chat_id."""

    @free_fall_migration(document_models=[AntifloodModel])
    async def rollback(self, session) -> None:
        collection = AntifloodModel.get_pymongo_collection()

        async for doc in collection.find():
            if "chat" in doc:
                chat_iid = doc["chat"]
                chat = await ChatModel.find_one(ChatModel.iid == chat_iid)
                if chat:
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"chat_id": chat.tid}, "$unset": {"chat": ""}},
                        session=session,
                    )

            actions = doc.get("actions") or []
            if is_forward_migrated_antiflood_action(actions):
                await collection.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"action": MODERN_TO_LEGACY_ACTIONS[actions[0]["name"]], "actions": []}},
                    session=session,
                )
