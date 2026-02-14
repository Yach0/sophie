"""Migration: populate_note_links

Description:
    Populates the 'chat' Link field in NoteModel using the existing 'chat_id' field.

Affected Collections:
    - notes

Impact:
    - Low risk: Only populates an optional Link field.
    - Large collection: Notes can be numerous.
"""

from __future__ import annotations

from beanie import free_fall_migration
from bson import DBRef
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.utils.logger import log


class Forward:
    """Populate chat Link from chat_id."""

    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def migrate(self, session):
        collection = NoteModel.get_pymongo_collection()
        async for doc in collection.find():
            if "chat" not in doc or doc["chat"] is None:
                chat_id = doc.get("chat_id")
                if chat_id:
                    chat = await ChatModel.find_one(ChatModel.tid == chat_id)
                    if chat:
                        await collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"chat": DBRef("chats", chat.id)}},
                            session=session,
                        )
                    else:
                        log.warning(
                            "Deleting orphaned note record without corresponding chat",
                            chat_id=chat_id,
                            doc_id=doc["_id"],
                        )
                        await collection.delete_one({"_id": doc["_id"]}, session=session)


class Backward:
    """Clear chat Link field."""

    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def rollback(self, session):
        collection = NoteModel.get_pymongo_collection()
        await collection.update_many({}, {"$unset": {"chat": ""}}, session=session)
