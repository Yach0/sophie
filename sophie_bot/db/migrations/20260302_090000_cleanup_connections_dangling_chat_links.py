"""Migration: cleanup_connections_dangling_chat_links

Description:
    Repairs connection records that reference deleted chats.
    If active `chat` link points to missing chat document, user is disconnected.
    `history` is also pruned from missing chat references.

Affected Collections:
    - connections
    - chats

Impact:
    - Low risk: only removes invalid references to missing chats
    - Backward compatible: keeps valid links unchanged
"""

from __future__ import annotations

from bson import DBRef

from beanie import free_fall_migration

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.utils.logger import log


class Forward:
    """Cleanup dangling chat links in connections."""

    @free_fall_migration(document_models=[ChatConnectionModel, ChatModel])
    async def cleanup(self, session) -> None:
        connections_collection = ChatConnectionModel.get_pymongo_collection()
        chats_collection = ChatModel.get_pymongo_collection()

        disconnected_count = 0
        pruned_history_links_count = 0

        async for connection_document in connections_collection.find():
            updates: dict[str, object] = {}

            chat_reference = connection_document.get("chat")
            if chat_reference is not None:
                chat_iid = chat_reference.id if isinstance(chat_reference, DBRef) else chat_reference
                chat_document = await chats_collection.find_one({"_id": chat_iid})

                if chat_document is None:
                    updates["chat"] = None
                    updates["expires_at"] = None
                    disconnected_count += 1

            history_references = connection_document.get("history")
            if isinstance(history_references, list):
                valid_history_references: list[object] = []
                invalid_history_reference_count = 0

                for history_reference in history_references:
                    history_chat_iid = (
                        history_reference.id if isinstance(history_reference, DBRef) else history_reference
                    )
                    history_chat_document = await chats_collection.find_one({"_id": history_chat_iid})
                    if history_chat_document is None:
                        invalid_history_reference_count += 1
                    else:
                        valid_history_references.append(history_reference)

                if invalid_history_reference_count > 0:
                    updates["history"] = valid_history_references
                    pruned_history_links_count += invalid_history_reference_count

            if updates:
                await connections_collection.update_one(
                    {"_id": connection_document["_id"]},
                    {"$set": updates},
                    session=session,
                )

        if disconnected_count > 0 or pruned_history_links_count > 0:
            log.info(
                "Cleanup dangling connection links completed",
                disconnected_count=disconnected_count,
                pruned_history_links_count=pruned_history_links_count,
            )


class Backward:
    """No rollback required for cleanup migration."""

    @free_fall_migration(document_models=[ChatConnectionModel])
    async def noop(self, session) -> None:
        del session
