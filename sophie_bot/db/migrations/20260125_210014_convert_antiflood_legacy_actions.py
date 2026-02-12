"""Migration: convert_antiflood_legacy_actions

Description:
    Converts legacy AntifloodModel action (string) to modern actions (list of FilterActionType).

Affected Collections:
    - antiflood

Impact:
    - Low risk: Normalizes action format.
    - Small collection: Antiflood settings are per-chat.
"""

from beanie import free_fall_migration
from sophie_bot.db.models.antiflood import AntifloodModel, LEGACY_ACTIONS, LEGACY_ACTIONS_TO_MODERN
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.utils.logger import log


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
    async def rollback(self, session):
        collection = AntifloodModel.get_pymongo_collection()
        # Inverse mapping
        MODERN_TO_LEGACY = {v: k for k, v in LEGACY_ACTIONS_TO_MODERN.items()}

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

            actions = doc.get("actions", [])
            if actions and len(actions) == 1:
                action_name = actions[0].get("name")
                if action_name in MODERN_TO_LEGACY:
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"action": MODERN_TO_LEGACY[action_name], "actions": []}},
                        session=session,
                    )
