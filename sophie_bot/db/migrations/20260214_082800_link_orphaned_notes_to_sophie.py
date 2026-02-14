"""Migration: link_orphaned_notes_to_sophie

Description:
    Links notes with orphaned created_user (integer IDs not found in chats collection)
    to a Sophie system chat entry as a fallback.

Affected Collections:
    - notes
    - chats (creates Sophie system entry if not exists)
"""

from __future__ import annotations

from datetime import datetime, timezone
from bson import DBRef
from beanie import free_fall_migration
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.notes import NoteModel

# Sophie bot's own Telegram ID - you may need to adjust this
# Using 0 or a special ID to represent Sophie system
SOPHIE_SYSTEM_TID = 0


class Forward:
    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def migrate(self, session):
        col = NoteModel.get_pymongo_collection()

        # Find or create Sophie system chat entry
        sophie_chat = await ChatModel.find_one(ChatModel.tid == SOPHIE_SYSTEM_TID)

        if not sophie_chat:
            # Create Sophie system chat entry
            sophie_chat = ChatModel(
                tid=SOPHIE_SYSTEM_TID,
                type=ChatType.private,
                first_name_or_title="Sophie",
                last_name=None,
                username="sophie_bot",
                is_bot=True,
                last_saw=datetime.now(timezone.utc),
            )
            await sophie_chat.save(session=session)

        sophie_ref = DBRef("chats", sophie_chat.id)

        # Find all notes with integer created_user and link them to Sophie
        async for doc in col.find({"created_user": {"$type": "int"}}):
            await col.update_one({"_id": doc["_id"]}, {"$set": {"created_user": sophie_ref}}, session=session)

        # Also handle edited_user if needed
        async for doc in col.find({"edited_user": {"$type": "int"}}):
            await col.update_one({"_id": doc["_id"]}, {"$set": {"edited_user": sophie_ref}}, session=session)


class Backward:
    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def rollback(self, session):
        # Find Sophie system chat
        sophie_chat = await ChatModel.find_one(ChatModel.tid == SOPHIE_SYSTEM_TID)
        if not sophie_chat:
            return

        col = NoteModel.get_pymongo_collection()
        sophie_ref = DBRef("chats", sophie_chat.id)

        # Convert notes linked to Sophie back to the integer value
        # We use SOPHIE_SYSTEM_TID as the integer value since we don't have original
        async for doc in col.find({"created_user": sophie_ref}):
            await col.update_one({"_id": doc["_id"]}, {"$set": {"created_user": SOPHIE_SYSTEM_TID}}, session=session)

        async for doc in col.find({"edited_user": sophie_ref}):
            await col.update_one({"_id": doc["_id"]}, {"$set": {"edited_user": SOPHIE_SYSTEM_TID}}, session=session)
