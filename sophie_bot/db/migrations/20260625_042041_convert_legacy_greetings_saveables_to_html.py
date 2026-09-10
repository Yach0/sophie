"""Migration: convert_legacy_greetings_saveables_to_html

Description:
    Converts legacy greetings Saveables (welcome message, welcome security
    message, and join request message) stored with parse_mode="md" to HTML
    text, normalizes legacy inline button syntax into stored button rows, and
    marks each Saveable as version 2.

    The notes migration (20260522_120000_convert_legacy_notes_to_html) only
    converted the standalone ``notes`` collection. The ``GreetingsModel``
    embeds its own ``Saveable`` documents (``note``, ``security_note``,
    ``join_request_message``), which were left as raw legacy markdown. As a
    result default/legacy welcome and welcome-security messages rendered their
    ``**bold**``, ``__underline__`` and ``[text](btnwelcomesecurity)`` markup
    literally instead of as formatted text and inline buttons.

Affected Collections:
    - greetings

Impact:
    - Low/Medium risk, small collection.
    - Existing markdown welcome Saveables are rendered once during migration
      instead of at send time.
    - Existing legacy inline buttons are parsed once during migration instead
      of at send time.
    - Rollback restores the original markdown text/button data from the
      per-Saveable migration metadata.
"""

from __future__ import annotations

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.greetings import GreetingsModel
from sophie_bot.db.models.notes import SaveableParseMode
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.modules.notes.utils.buttons.compat import parse_legacy_text_buttons
from sophie_bot.modules.notes.utils.buttons.models import ButtonLayout

GREETINGS_SAVEABLE_FIELDS: tuple[str, ...] = ("note", "security_note", "join_request_message")

LEGACY_MARKDOWN_TEXT_FIELD = "legacy_markdown_text"
LEGACY_BUTTONS_FIELD = "legacy_buttons"


def _drop_captcha_buttons(layout: ButtonLayout) -> ButtonLayout:
    """Drop captcha (welcome security) buttons.

    The captcha button is always hard-added in code (the welcome security flow
    appends an "I am not a robot" button as an additional keyboard), so keeping
    a captcha button parsed from the message text would render it twice.
    """
    filtered = ButtonLayout()
    for row in layout:
        kept_row: list[Button] = [button for button in row if button.action != ButtonAction.captcha]
        if kept_row:
            filtered.append(kept_row)
    return filtered


def convert_legacy_saveable_to_html(saveable: dict[str, Any]) -> dict[str, Any] | None:
    """Return a converted copy of a legacy Saveable subdocument, or None to skip."""
    text = saveable.get("text")
    if not isinstance(text, str):
        return None

    should_convert_markdown = saveable.get("parse_mode") == SaveableParseMode.markdown.value
    should_convert_buttons = saveable.get("version") == 1
    if not should_convert_markdown and not should_convert_buttons:
        return None

    converted = dict(saveable)
    converted["version"] = 2
    normalized_text = text

    if should_convert_buttons:
        normalized_text, parsed_buttons = parse_legacy_text_buttons(normalized_text)
        parsed_buttons = _drop_captcha_buttons(parsed_buttons)
        converted["buttons"] = [[button.model_dump(mode="json") for button in row] for row in parsed_buttons]
        converted[LEGACY_BUTTONS_FIELD] = saveable.get("buttons", [])

    if should_convert_markdown:
        converted["text"] = ai_markdown_to_html(normalized_text)
        converted["parse_mode"] = SaveableParseMode.html.value
        converted[LEGACY_MARKDOWN_TEXT_FIELD] = text
    else:
        converted["text"] = normalized_text

    return converted


def restore_legacy_saveable(saveable: dict[str, Any]) -> dict[str, Any] | None:
    """Return a restored copy of a converted Saveable subdocument, or None to skip."""
    has_legacy_markdown = isinstance(saveable.get(LEGACY_MARKDOWN_TEXT_FIELD), str)
    has_legacy_buttons = LEGACY_BUTTONS_FIELD in saveable
    if not (has_legacy_markdown or has_legacy_buttons):
        return None

    restored = dict(saveable)
    restored.pop(LEGACY_MARKDOWN_TEXT_FIELD, None)
    restored.pop(LEGACY_BUTTONS_FIELD, None)
    restored["version"] = 1

    if has_legacy_markdown:
        restored["text"] = saveable[LEGACY_MARKDOWN_TEXT_FIELD]
        restored["parse_mode"] = SaveableParseMode.markdown.value
    if has_legacy_buttons:
        restored["buttons"] = saveable.get(LEGACY_BUTTONS_FIELD, [])

    return restored


def build_greetings_saveables_migration_update(document: dict[str, Any]) -> dict[str, Any] | None:
    set_fields: dict[str, Any] = {}
    for field in GREETINGS_SAVEABLE_FIELDS:
        saveable = document.get(field)
        if not isinstance(saveable, dict):
            continue
        converted = convert_legacy_saveable_to_html(saveable)
        if converted is not None:
            set_fields[field] = converted

    if not set_fields:
        return None

    return {"$set": set_fields}


def build_greetings_saveables_rollback_update(document: dict[str, Any]) -> dict[str, Any] | None:
    set_fields: dict[str, Any] = {}
    for field in GREETINGS_SAVEABLE_FIELDS:
        saveable = document.get(field)
        if not isinstance(saveable, dict):
            continue
        restored = restore_legacy_saveable(saveable)
        if restored is not None:
            set_fields[field] = restored

    if not set_fields:
        return None

    return {"$set": set_fields}


class Forward:
    """Convert legacy markdown greetings Saveables to HTML with parsed buttons."""

    @free_fall_migration(document_models=[GreetingsModel])
    async def migrate(self, session: Any) -> None:
        collection = GreetingsModel.get_pymongo_collection()
        async for document in collection.find(session=session):
            update = build_greetings_saveables_migration_update(document)
            if update:
                await collection.update_one({"_id": document["_id"]}, update, session=session)


class Backward:
    """Restore original markdown text and buttons for greetings Saveables."""

    @free_fall_migration(document_models=[GreetingsModel])
    async def rollback(self, session: Any) -> None:
        collection = GreetingsModel.get_pymongo_collection()
        async for document in collection.find(session=session):
            update = build_greetings_saveables_rollback_update(document)
            if update:
                await collection.update_one({"_id": document["_id"]}, update, session=session)
