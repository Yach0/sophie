"""Migration: relink_legacy_int64_note_users

Description:
    Cleans up notes whose `created_user`/`edited_user` still hold a raw Telegram user ID instead of
    a link to the chats collection (the pre-4.0 schema).

    Two earlier migrations were supposed to have handled this:
      - 20260201_020300_convert_notes_users_to_links skips users missing from `chats`.
      - 20260214_082800_link_orphaned_notes_to_sophie was the fallback, but queried
        `{"$type": "int"}`, which in MongoDB matches only 32-bit ints and never `long`.

    Any Telegram ID above 2^31-1 (~2.1 billion, i.e. most modern accounts) is stored as a BSON
    long, so the fallback never matched it and those rows survived. Reading them raises
    `ValidationError: Id must be of type PydanticObjectId` (SOPHIE-285).

    This resolves the ID against `chats` to preserve attribution where possible, and unsets it
    otherwise -- matching NoteModel._coerce_legacy_user_link, which reads unknown attribution as
    None rather than misattributing the note.

Affected Collections:
    - notes

Impact:
    - Low risk; touches only documents that cannot currently be read at all.
    - Small number of documents (legacy rows only).
    - Backward is a no-op: the original IDs are not recoverable once relinked.
"""

from __future__ import annotations

from typing import Any

from beanie import free_fall_migration
from bson import DBRef

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.utils.logger import log

_USER_FIELDS = ("created_user", "edited_user")

# "int" alone matches only 32-bit ints; modern Telegram IDs are stored as "long".
_LEGACY_ID_TYPES = ("int", "long")


def legacy_id_query(field: str) -> dict[str, Any]:
    """Match a field holding a raw Telegram ID, whether stored as a 32-bit int or a BSON long.

    Written as an $or of scalar $type checks rather than the equivalent `{"$type": ["int", "long"]}`
    list form, which mongomock cannot evaluate -- this way the predicate is covered by tests.
    """
    return {"$or": [{field: {"$type": bson_type}} for bson_type in _LEGACY_ID_TYPES]}


async def relink_legacy_note_users(notes: Any, session: Any = None) -> tuple[int, int]:
    """Relink legacy integer note users, unsetting them when the user is unknown.

    Returns (relinked, cleared). Kept as a plain function so it can be driven directly by tests.

    Notes are read through the raw collection because the legacy rows fail NoteModel validation,
    but chats are resolved through Beanie: `ChatModel.tid` is stored under its `chat_id` alias, so
    a raw `{"tid": ...}` query silently matches nothing and clears every note's attribution.
    """
    relinked = 0
    cleared = 0

    for field in _USER_FIELDS:
        async for note in notes.find(legacy_id_query(field), session=session):
            chat = await ChatModel.find_one(ChatModel.tid == note[field], session=session)

            if chat:
                update = {"$set": {field: DBRef("chats", chat.iid)}}
                relinked += 1
            else:
                update = {"$unset": {field: ""}}
                cleared += 1

            await notes.update_one({"_id": note["_id"]}, update, session=session)

    if relinked or cleared:
        log.info("Relinked legacy note users", relinked=relinked, cleared=cleared)

    return relinked, cleared


class Forward:
    """Relink legacy integer note users, or unset them when the user is unknown."""

    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def relink(self, session) -> None:
        await relink_legacy_note_users(NoteModel.get_pymongo_collection(), session=session)


class Backward:
    """No rollback: the original Telegram IDs are not stored once the field is relinked."""

    @free_fall_migration(document_models=[NoteModel, ChatModel])
    async def noop(self, session) -> None:
        del session
