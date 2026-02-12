"""Migration: convert_connections_to_links

Description:
    Converts ChatConnectionModel fields user_id (int) to user (Link[ChatModel]),
    chat_id (int) to chat (Link[ChatModel]), and history (list[int]) to history (list[Link[ChatModel]]).

Affected Collections:
    - connections
"""

from bson import DBRef
from pymongo.errors import OperationFailure

from beanie import free_fall_migration
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.utils.logger import log


class Forward:
    @free_fall_migration(document_models=[ChatConnectionModel])
    async def migrate(self, session):
        collection = ChatConnectionModel.get_pymongo_collection()

        # Drop unique index on legacy fields if it exists
        try:
            await collection.drop_index("legacy_user_id_chat_id")
        except OperationFailure:
            pass

        async for doc in collection.find():
            updates = {}
            unsets = {}

            # user_id -> user
            if "user_id" in doc:
                user_id = doc["user_id"]
                user = await ChatModel.find_one(ChatModel.tid == user_id)
                if user:
                    updates["user"] = DBRef("chats", user.id)
                unsets["user_id"] = ""

            # chat_id -> chat
            if "chat_id" in doc and doc["chat_id"] is not None:
                chat_id = doc["chat_id"]
                chat = await ChatModel.find_one(ChatModel.tid == chat_id)
                if chat:
                    updates["chat"] = DBRef("chats", chat.id)
                unsets["chat_id"] = ""
            elif "chat_id" in doc:
                unsets["chat_id"] = ""

            # history [int] -> history [Link]
            if "history" in doc:
                history_ids = doc["history"]
                new_history = []
                for h_id in history_ids:
                    h_chat = await ChatModel.find_one(ChatModel.tid == h_id)
                    if h_chat:
                        new_history.append(DBRef("chats", h_chat.id))
                updates["history"] = new_history

            if updates or unsets:
                # If user is missing but was required, we must delete the record
                if "user_id" in doc and "user" not in updates:
                    log.warning(
                        "Deleting orphaned connection record without user",
                        user_id=doc.get("user_id"),
                        doc_id=doc["_id"],
                    )
                    await collection.delete_one({"_id": doc["_id"]}, session=session)
                    continue

                final_update = {}
                if updates:
                    final_update["$set"] = updates
                if unsets:
                    final_update["$unset"] = unsets

                await collection.update_one({"_id": doc["_id"]}, final_update, session=session)


class Backward:
    @free_fall_migration(document_models=[ChatConnectionModel])
    async def rollback(self, session):
        collection = ChatConnectionModel.get_pymongo_collection()
        async for doc in collection.find():
            updates = {}
            unsets = {}

            if "user" in doc:
                # Handle both DBRef and raw ObjectId for backward compatibility
                user_id = doc["user"].id if isinstance(doc["user"], DBRef) else doc["user"]
                user = await ChatModel.find_one(ChatModel.iid == user_id)
                if user:
                    updates["user_id"] = user.tid
                unsets["user"] = ""

            if "chat" in doc:
                # Handle both DBRef and raw ObjectId for backward compatibility
                chat_id = doc["chat"].id if isinstance(doc["chat"], DBRef) else doc["chat"]
                chat = await ChatModel.find_one(ChatModel.iid == chat_id)
                if chat:
                    updates["chat_id"] = chat.tid
                unsets["chat"] = ""

            if "history" in doc:
                new_history = []
                for h_ref in doc["history"]:
                    # Handle both DBRef and raw ObjectId for backward compatibility
                    h_id = h_ref.id if isinstance(h_ref, DBRef) else h_ref
                    h_chat = await ChatModel.find_one(ChatModel.iid == h_id)
                    if h_chat:
                        new_history.append(h_chat.tid)
                updates["history"] = new_history

            if updates or unsets:
                final_update = {}
                if updates:
                    final_update["$set"] = updates
                if unsets:
                    final_update["$unset"] = unsets
                await collection.update_one({"_id": doc["_id"]}, final_update, session=session)
