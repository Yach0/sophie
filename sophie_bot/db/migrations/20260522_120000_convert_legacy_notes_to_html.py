"""Migration: convert_legacy_notes_to_html

Description:
    Converts legacy NoteModel text stored with parse_mode="md" to HTML text and
    marks the note as parse_mode="html".

Affected Collections:
    - notes

Impact:
    - Existing markdown notes are rendered once during migration instead of at
      note send time.
    - Rollback restores the original markdown text from migration metadata.
"""

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.notes import CURRENT_SAVEABLE_VERSION, NoteModel, SaveableParseMode
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html

LEGACY_MARKDOWN_TEXT_FIELD = "legacy_markdown_text"


def convert_legacy_note_to_html_update(document: dict[str, Any]) -> dict[str, Any] | None:
    if document.get("parse_mode") != SaveableParseMode.markdown.value:
        return None

    text = document.get("text")
    if not isinstance(text, str):
        return None

    return {
        "$set": {
            "text": ai_markdown_to_html(text),
            "parse_mode": SaveableParseMode.html.value,
            "version": CURRENT_SAVEABLE_VERSION,
            LEGACY_MARKDOWN_TEXT_FIELD: text,
        }
    }


def restore_legacy_note_markdown_update(document: dict[str, Any]) -> dict[str, Any] | None:
    text = document.get(LEGACY_MARKDOWN_TEXT_FIELD)
    if not isinstance(text, str):
        return None

    return {
        "$set": {
            "text": text,
            "parse_mode": SaveableParseMode.markdown.value,
            "version": 1,
        },
        "$unset": {
            LEGACY_MARKDOWN_TEXT_FIELD: "",
        },
    }


class Forward:
    @free_fall_migration(document_models=[NoteModel])
    async def migrate(self, session: Any) -> None:
        collection = NoteModel.get_pymongo_collection()
        async for document in collection.find({"parse_mode": SaveableParseMode.markdown.value}):
            update = convert_legacy_note_to_html_update(document)
            if update:
                await collection.update_one({"_id": document["_id"]}, update, session=session)


class Backward:
    @free_fall_migration(document_models=[NoteModel])
    async def rollback(self, session: Any) -> None:
        collection = NoteModel.get_pymongo_collection()
        async for document in collection.find({LEGACY_MARKDOWN_TEXT_FIELD: {"$exists": True}}):
            update = restore_legacy_note_markdown_update(document)
            if update:
                await collection.update_one({"_id": document["_id"]}, update, session=session)
