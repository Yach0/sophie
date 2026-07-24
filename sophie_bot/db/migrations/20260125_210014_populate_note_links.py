"""Migration: populate_note_links

Description:
    Populates the 'chat' Link field in NoteModel using the existing 'chat_id' field.

Affected Collections:
    - notes

Impact:
    - Low risk: Only populates an optional Link field.
    - Large collection: Notes can be numerous.
    - Backward is a no-op; see below.

Rollback:
    Not possible, and not needed. Forward is purely additive: it derives `chat` from
    `chat_id` and never removes `chat_id`. It only ever wrote `chat` where the field was
    absent, so its set is the notes that predate the Link schema -- which nothing records.

    `chat_id` is not a legacy leftover that marks those notes: it is `NoteModel.chat_tid`,
    a required field (`Field(..., alias="chat_id")`) that every note still carries, written
    at every creation site. So it cannot distinguish Forward's notes from later ones.

    The previous Backward was `update_many({}, {"$unset": {"chat": ""}})` -- unfiltered. It
    stripped `chat` from *every* note, including all notes created since, which `NoteModel`
    requires and which Forward never touched. That left the whole collection unreadable, and
    re-running Forward to rebuild the links would *delete* any note whose `chat_id` no longer
    resolves to a chat.

    Leaving `chat` populated is harmless for pre-migration code, which simply ignores the
    field, so Backward does nothing rather than orphan the collection.
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
    """No rollback: notes Forward linked are indistinguishable from notes created since."""

    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def noop(self, session) -> None:
        del session
