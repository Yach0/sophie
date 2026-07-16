"""Migration: link_orphaned_notes_to_sophie

Description:
    Links notes with orphaned created_user (integer IDs not found in chats collection)
    to a Sophie system chat entry as a fallback.

Affected Collections:
    - notes
    - chats (creates Sophie system entry if not exists)

Impact:
    - Backward is a no-op; see below.

Rollback:
    Not possible. Forward overwrites each orphaned user ID with a link to the Sophie system
    chat, collapsing many distinct original IDs into one reference and preserving none of
    them. Nothing records what any note's user ID was.

    The previous Backward admitted this in a comment -- "We use SOPHIE_SYSTEM_TID as the
    integer value since we don't have original" -- and wrote the literal 0 into every note
    pointing at the Sophie chat. That did not restore anything: it replaced an honest
    "attributed to Sophie" link with a fabricated ID, and it also rewrote notes genuinely
    authored by Sophie, which Forward never touched.

    Leaving the Sophie link in place at least keeps the attribution truthful, so Backward
    does nothing.
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
    """No rollback: Forward overwrote the original user IDs without recording them."""

    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def noop(self, session) -> None:
        del session
