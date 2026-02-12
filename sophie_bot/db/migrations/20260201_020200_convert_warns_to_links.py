"""Migration: convert_warns_to_links

Description:
    Converts WarnModel fields chat_id (int), user_id (int) and admin_id (int) to chat (Link[ChatModel]), user (Link[ChatModel]) and admin (Link[ChatModel]).

Affected Collections:
    - warns
"""

from bson import DBRef

from beanie import free_fall_migration
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel


class Forward:
    @free_fall_migration(document_models=[WarnModel])
    async def migrate(self, session):
        collection = WarnModel.get_pymongo_collection()
        async for doc in collection.find():
            updates = {}
            unsets = {}

            if "chat_id" in doc:
                chat = await ChatModel.find_one(ChatModel.tid == doc["chat_id"])
                if chat:
                    updates["chat"] = DBRef("chats", chat.id)
                unsets["chat_id"] = ""

            if "user_id" in doc:
                user = await ChatModel.find_one(ChatModel.tid == doc["user_id"])
                if user:
                    updates["user"] = DBRef("chats", user.id)
                unsets["user_id"] = ""

            if "admin_id" in doc:
                admin = await ChatModel.find_one(ChatModel.tid == doc["admin_id"])
                if admin:
                    updates["admin"] = DBRef("chats", admin.id)
                unsets["admin_id"] = ""

            if updates or unsets:
                # If chat, user or admin is missing, we must delete the record as they are required
                if (
                    ("chat_id" in doc and "chat" not in updates)
                    or ("user_id" in doc and "user" not in updates)
                    or ("admin_id" in doc and "admin" not in updates)
                ):
                    await collection.delete_one({"_id": doc["_id"]}, session=session)
                    continue

                u = {}
                if updates:
                    u["$set"] = updates
                if unsets:
                    u["$unset"] = unsets
                await collection.update_one({"_id": doc["_id"]}, u, session=session)


class Backward:
    @free_fall_migration(document_models=[WarnModel])
    async def rollback(self, session):
        collection = WarnModel.get_pymongo_collection()
        async for doc in collection.find():
            updates = {}
            unsets = {}

            if "chat" in doc:
                chat_id = doc["chat"].id if isinstance(doc["chat"], DBRef) else doc["chat"]
                chat = await ChatModel.find_one(ChatModel.iid == chat_id)
                if chat:
                    updates["chat_id"] = chat.tid
                unsets["chat"] = ""

            if "user" in doc:
                user_id = doc["user"].id if isinstance(doc["user"], DBRef) else doc["user"]
                user = await ChatModel.find_one(ChatModel.iid == user_id)
                if user:
                    updates["user_id"] = user.tid
                unsets["user"] = ""

            if "admin" in doc:
                admin_id = doc["admin"].id if isinstance(doc["admin"], DBRef) else doc["admin"]
                admin = await ChatModel.find_one(ChatModel.iid == admin_id)
                if admin:
                    updates["admin_id"] = admin.tid
                unsets["admin"] = ""

            if updates or unsets:
                u = {}
                if updates:
                    u["$set"] = updates
                if unsets:
                    u["$unset"] = unsets
                await collection.update_one({"_id": doc["_id"]}, u, session=session)
