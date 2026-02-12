"""Migration: convert_notes_users_to_links

Description:
    Converts NoteModel fields created_user (int) and edited_user (int) to Link[ChatModel].

Affected Collections:
    - notes
"""

from bson import DBRef

from beanie import free_fall_migration
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel


class Forward:
    @free_fall_migration(document_models=[NoteModel])
    async def migrate(self, session):
        col = NoteModel.get_pymongo_collection()
        async for doc in col.find():
            updates = {}

            if "created_user" in doc and isinstance(doc["created_user"], int):
                c = await ChatModel.find_one(ChatModel.tid == doc["created_user"])
                if c:
                    updates["created_user"] = DBRef("chats", c.id)

            if "edited_user" in doc and isinstance(doc["edited_user"], int):
                c = await ChatModel.find_one(ChatModel.tid == doc["edited_user"])
                if c:
                    updates["edited_user"] = DBRef("chats", c.id)

            if updates:
                await col.update_one({"_id": doc["_id"]}, {"$set": updates}, session=session)


class Backward:
    @free_fall_migration(document_models=[NoteModel])
    async def rollback(self, session):
        col = NoteModel.get_pymongo_collection()
        async for doc in col.find():
            updates = {}

            if "created_user" in doc:
                created_user_id = (
                    doc["created_user"].id if isinstance(doc["created_user"], DBRef) else doc["created_user"]
                )
                c = await ChatModel.find_one(ChatModel.iid == created_user_id)
                if c:
                    updates["created_user"] = c.tid

            if "edited_user" in doc:
                edited_user_id = doc["edited_user"].id if isinstance(doc["edited_user"], DBRef) else doc["edited_user"]
                c = await ChatModel.find_one(ChatModel.iid == edited_user_id)
                if c:
                    updates["edited_user"] = c.tid

            if updates:
                await col.update_one({"_id": doc["_id"]}, {"$set": updates}, session=session)
