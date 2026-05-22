"""Migration: convert_legacy_notes_to_html

Description:
    Converts legacy NoteModel text stored with parse_mode="md" to HTML text,
    normalizes legacy inline button syntax into stored button rows, and marks
    the note as version 2.

Affected Collections:
    - notes

Impact:
    - Existing markdown notes are rendered once during migration instead of at
      note send time.
    - Existing legacy inline buttons are parsed once during migration instead
      of at note send time.
    - Rollback restores the original text/button data from migration metadata.
"""

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.notes import CURRENT_SAVEABLE_VERSION, NoteModel, SaveableParseMode
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.modules.notes.utils.buttons.compat import parse_legacy_text_buttons

LEGACY_MARKDOWN_TEXT_FIELD = "legacy_markdown_text"
LEGACY_BUTTONS_FIELD = "legacy_buttons"


def convert_legacy_note_to_html_update(document: dict[str, Any]) -> dict[str, Any] | None:
    text = document.get("text")
    if not isinstance(text, str):
        return None

    should_convert_markdown = document.get("parse_mode") == SaveableParseMode.markdown.value
    should_convert_buttons = document.get("version") == 1
    if not should_convert_markdown and not should_convert_buttons:
        return None

    normalized_text = text
    set_fields: dict[str, Any] = {
        "version": CURRENT_SAVEABLE_VERSION,
    }

    if should_convert_buttons:
        normalized_text, parsed_buttons = parse_legacy_text_buttons(normalized_text)
        set_fields["buttons"] = [[button.model_dump(mode="json") for button in row] for row in parsed_buttons]
        set_fields[LEGACY_BUTTONS_FIELD] = document.get("buttons", [])

    if should_convert_markdown:
        set_fields["text"] = ai_markdown_to_html(normalized_text)
        set_fields["parse_mode"] = SaveableParseMode.html.value
        set_fields[LEGACY_MARKDOWN_TEXT_FIELD] = text
    else:
        set_fields["text"] = normalized_text

    return {
        "$set": set_fields,
    }


def restore_legacy_note_markdown_update(document: dict[str, Any]) -> dict[str, Any] | None:
    text = document.get(LEGACY_MARKDOWN_TEXT_FIELD, document.get("text"))
    has_legacy_markdown = isinstance(document.get(LEGACY_MARKDOWN_TEXT_FIELD), str)
    has_legacy_buttons = LEGACY_BUTTONS_FIELD in document
    if not isinstance(text, str) or not (has_legacy_markdown or has_legacy_buttons):
        return None

    set_fields: dict[str, Any] = {
        "text": text,
        "version": 1,
    }
    if has_legacy_markdown:
        set_fields["parse_mode"] = SaveableParseMode.markdown.value
    if has_legacy_buttons:
        set_fields["buttons"] = document.get(LEGACY_BUTTONS_FIELD, [])

    return {
        "$set": set_fields,
        "$unset": {
            LEGACY_MARKDOWN_TEXT_FIELD: "",
            LEGACY_BUTTONS_FIELD: "",
        },
    }


class Forward:
    @free_fall_migration(document_models=[NoteModel])
    async def migrate(self, session: Any) -> None:
        collection = NoteModel.get_pymongo_collection()
        async for document in collection.find(
            {"$or": [{"parse_mode": SaveableParseMode.markdown.value}, {"version": 1}]}
        ):
            update = convert_legacy_note_to_html_update(document)
            if update:
                await collection.update_one({"_id": document["_id"]}, update, session=session)


class Backward:
    @free_fall_migration(document_models=[NoteModel])
    async def rollback(self, session: Any) -> None:
        collection = NoteModel.get_pymongo_collection()
        async for document in collection.find(
            {"$or": [{LEGACY_MARKDOWN_TEXT_FIELD: {"$exists": True}}, {LEGACY_BUTTONS_FIELD: {"$exists": True}}]}
        ):
            update = restore_legacy_note_markdown_update(document)
            if update:
                await collection.update_one({"_id": document["_id"]}, update, session=session)
