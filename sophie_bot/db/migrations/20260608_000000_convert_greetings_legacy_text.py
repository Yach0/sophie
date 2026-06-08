"""Migration: convert_greetings_legacy_text

Description:
    Converts legacy GreetingsModel Saveable text fields from markdown to HTML,
    removes stale legacy button syntax from security_note and join_request_message,
    and normalizes extracted buttons into stored button rows.

Affected Collections:
    - greetings

Impact:
    - Existing markdown greetings text is converted to HTML once during migration.
    - Legacy `[text](btnwelcomesecurity)` syntax is stripped from security_note
      and the captcha button is dropped (code always provides it as
      additional_keyboard).
    - Legacy `[text](btnsophieurl)` syntax is extracted from join_request_message
      and stored as a proper sophiedm Button in the Saveable's buttons field.
    - Rollback restores the original markdown text and button data from migration
      metadata.
"""

from __future__ import annotations

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.greetings import GreetingsModel
from sophie_bot.db.models.notes import CURRENT_SAVEABLE_VERSION, SaveableParseMode
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.modules.notes.utils.buttons.compat import parse_legacy_text_buttons

LEGACY_MARKDOWN_TEXT_FIELD = "legacy_markdown_text"
LEGACY_BUTTONS_FIELD = "legacy_buttons"

GREETING_SAVEABLE_FIELDS = ("note", "security_note", "join_request_message")


def _convert_saveable_field(
    saveable: dict[str, Any],
    field_name: str,
) -> dict[str, Any] | None:
    """Build a $set update dict for a single Saveable subdocument field."""
    text = saveable.get("text")
    if not isinstance(text, str):
        return None

    should_convert_markdown = saveable.get("parse_mode") == SaveableParseMode.markdown.value
    should_convert_buttons = saveable.get("version", 1) < CURRENT_SAVEABLE_VERSION

    if not should_convert_markdown and not should_convert_buttons:
        return None

    normalized_text = text
    set_fields: dict[str, Any] = {
        f"{field_name}.version": CURRENT_SAVEABLE_VERSION,
    }

    if should_convert_buttons:
        normalized_text, parsed_buttons = parse_legacy_text_buttons(normalized_text)

        # Drop captcha buttons from security_note — the code always provides
        # the welcome security button via additional_keyboard.
        if field_name == "security_note":
            parsed_buttons = [[btn for btn in row if btn.action != ButtonAction.captcha] for row in parsed_buttons]
            parsed_buttons = [row for row in parsed_buttons if row]

        set_fields[f"{field_name}.buttons"] = [
            [button.model_dump(mode="json") for button in row] for row in parsed_buttons
        ]
        set_fields[f"{field_name}.{LEGACY_BUTTONS_FIELD}"] = saveable.get("buttons", [])

    if should_convert_markdown:
        set_fields[f"{field_name}.text"] = ai_markdown_to_html(normalized_text)
        set_fields[f"{field_name}.parse_mode"] = SaveableParseMode.html.value
        set_fields[f"{field_name}.{LEGACY_MARKDOWN_TEXT_FIELD}"] = text
    else:
        set_fields[f"{field_name}.text"] = normalized_text

    return set_fields


def _restore_saveable_field(
    saveable: dict[str, Any],
    field_name: str,
) -> dict[str, Any] | None:
    """Build a $set/$unset rollback dict for a single Saveable subdocument field."""
    has_legacy_markdown = isinstance(saveable.get(LEGACY_MARKDOWN_TEXT_FIELD), str)
    has_legacy_buttons = LEGACY_BUTTONS_FIELD in saveable

    if not has_legacy_markdown and not has_legacy_buttons:
        return None

    set_fields: dict[str, Any] = {}
    unset_fields: dict[str, Any] = {}

    if has_legacy_markdown:
        set_fields[f"{field_name}.text"] = saveable[LEGACY_MARKDOWN_TEXT_FIELD]
        set_fields[f"{field_name}.parse_mode"] = SaveableParseMode.markdown.value
        unset_fields[f"{field_name}.{LEGACY_MARKDOWN_TEXT_FIELD}"] = ""

    if has_legacy_buttons:
        set_fields[f"{field_name}.buttons"] = saveable[LEGACY_BUTTONS_FIELD]
        unset_fields[f"{field_name}.{LEGACY_BUTTONS_FIELD}"] = ""

    set_fields[f"{field_name}.version"] = 1

    result: dict[str, Any] = {"$set": set_fields}
    if unset_fields:
        result["$unset"] = unset_fields
    return result


def _merge_updates(updates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Merge multiple per-field update dicts into a single MongoDB update.

    Forward updates from _convert_saveable_field are flat dicts of field→value.
    Backward updates from _restore_saveable_field are wrapped with $set/$unset.
    """
    merged_set: dict[str, Any] = {}
    merged_unset: dict[str, Any] = {}

    for update in updates:
        if "$set" in update:
            merged_set.update(update["$set"])
            if "$unset" in update:
                merged_unset.update(update["$unset"])
        else:
            # Flat dict from _convert_saveable_field — all keys are $set fields
            merged_set.update(update)

    if not merged_set and not merged_unset:
        return None

    result: dict[str, Any] = {"$set": merged_set}
    if merged_unset:
        result["$unset"] = merged_unset
    return result


class Forward:
    """Convert greetings Saveable text fields from markdown to HTML and strip
    legacy button syntax."""

    @free_fall_migration(document_models=[GreetingsModel])
    async def migrate(self, session: Any) -> None:
        collection = GreetingsModel.get_pymongo_collection()

        query = {
            "$or": [{f"{field}.parse_mode": SaveableParseMode.markdown.value} for field in GREETING_SAVEABLE_FIELDS]
            + [{f"{field}.version": {"$lt": CURRENT_SAVEABLE_VERSION}} for field in GREETING_SAVEABLE_FIELDS]
        }

        async for document in collection.find(query, session=session):
            updates: list[dict[str, Any]] = []

            for field_name in GREETING_SAVEABLE_FIELDS:
                saveable = document.get(field_name)
                if not isinstance(saveable, dict):
                    continue
                field_update = _convert_saveable_field(saveable, field_name)
                if field_update:
                    updates.append(field_update)

            merged = _merge_updates(updates)
            if merged:
                await collection.update_one({"_id": document["_id"]}, merged, session=session)


class Backward:
    """Restore original markdown text and button data for greetings Saveable
    fields."""

    @free_fall_migration(document_models=[GreetingsModel])
    async def rollback(self, session: Any) -> None:
        collection = GreetingsModel.get_pymongo_collection()

        query = {
            "$or": [{f"{field}.{LEGACY_MARKDOWN_TEXT_FIELD}": {"$exists": True}} for field in GREETING_SAVEABLE_FIELDS]
            + [{f"{field}.{LEGACY_BUTTONS_FIELD}": {"$exists": True}} for field in GREETING_SAVEABLE_FIELDS]
        }

        async for document in collection.find(query, session=session):
            updates: list[dict[str, Any]] = []

            for field_name in GREETING_SAVEABLE_FIELDS:
                saveable = document.get(field_name)
                if not isinstance(saveable, dict):
                    continue
                field_update = _restore_saveable_field(saveable, field_name)
                if field_update:
                    updates.append(field_update)

            merged = _merge_updates(updates)
            if merged:
                await collection.update_one({"_id": document["_id"]}, merged, session=session)
