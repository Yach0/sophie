"""Test suite for database migrations."""

import importlib
from pathlib import Path
from types import ModuleType

import pytest


def _legacy_notes_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260522_120000_convert_legacy_notes_to_html")


def test_convert_legacy_notes_to_html_update_converts_markdown_note() -> None:
    migration = _legacy_notes_migration()

    update = migration.convert_legacy_note_to_html_update(
        {"_id": "note-id", "text": "**legacy** note", "parse_mode": "md", "version": 1}
    )

    assert update == {
        "$set": {
            "text": "<b>legacy</b> note",
            "parse_mode": "html",
            "version": 2,
            "legacy_markdown_text": "**legacy** note",
            "buttons": [],
            "legacy_buttons": [],
        }
    }


def test_convert_legacy_notes_to_html_update_extracts_legacy_buttons() -> None:
    migration = _legacy_notes_migration()

    update = migration.convert_legacy_note_to_html_update(
        {
            "_id": "note-id",
            "text": "Menu\n[Open](btnurl:https://example.com)\n[Rules](btnrules)",
            "parse_mode": "md",
            "version": 1,
        }
    )

    assert update == {
        "$set": {
            "text": "Menu",
            "parse_mode": "html",
            "version": 2,
            "legacy_markdown_text": "Menu\n[Open](btnurl:https://example.com)\n[Rules](btnrules)",
            "buttons": [
                [{"text": "Open", "action": "url", "data": "https://example.com", "style": None}],
                [{"text": "Rules", "action": "rules", "data": None, "style": None}],
            ],
            "legacy_buttons": [],
        }
    }


def test_convert_legacy_notes_to_html_update_extracts_buttons_from_html_v1_note() -> None:
    migration = _legacy_notes_migration()

    update = migration.convert_legacy_note_to_html_update(
        {"_id": "note-id", "text": "Menu\n[Delete](btndelmsg)", "parse_mode": "html", "version": 1}
    )

    assert update == {
        "$set": {
            "text": "Menu\n",
            "version": 2,
            "buttons": [[{"text": "Delete", "action": "delmsg", "data": None, "style": None}]],
            "legacy_buttons": [],
        }
    }


def test_convert_legacy_notes_to_html_update_skips_html_note() -> None:
    migration = _legacy_notes_migration()

    assert migration.convert_legacy_note_to_html_update({"text": "<b>modern</b>", "parse_mode": "html"}) is None


def test_restore_legacy_note_markdown_update_restores_original_text() -> None:
    migration = _legacy_notes_migration()

    update = migration.restore_legacy_note_markdown_update(
        {"text": "<b>legacy</b> note", "parse_mode": "html", "legacy_markdown_text": "**legacy** note"}
    )

    assert update == {
        "$set": {
            "text": "**legacy** note",
            "parse_mode": "md",
            "version": 1,
        },
        "$unset": {
            "legacy_markdown_text": "",
            "legacy_buttons": "",
        },
    }


def test_restore_legacy_note_markdown_update_restores_original_buttons() -> None:
    migration = _legacy_notes_migration()

    update = migration.restore_legacy_note_markdown_update(
        {
            "text": "Menu\n",
            "parse_mode": "html",
            "buttons": [[{"text": "Delete", "action": "delmsg"}]],
            "legacy_buttons": [[{"text": "Old", "action": "url", "data": "https://example.com"}]],
        }
    )

    assert update == {
        "$set": {
            "text": "Menu\n",
            "version": 1,
            "buttons": [[{"text": "Old", "action": "url", "data": "https://example.com"}]],
        },
        "$unset": {
            "legacy_markdown_text": "",
            "legacy_buttons": "",
        },
    }


def _legacy_greetings_migration() -> ModuleType:
    return importlib.import_module(
        "sophie_bot.db.migrations.20260625_042041_convert_legacy_greetings_saveables_to_html"
    )


def test_convert_legacy_greetings_saveables_converts_markdown_and_buttons() -> None:
    migration = _legacy_greetings_migration()

    update = migration.build_greetings_saveables_migration_update(
        {
            "_id": "greetings-id",
            "note": {"text": "**Welcome**", "parse_mode": "md", "version": 1},
            "security_note": {
                "text": "Prove you are human\n[I am not a robot](btnwelcomesecurity)",
                "parse_mode": "md",
                "version": 1,
            },
        }
    )

    assert update == {
        "$set": {
            "note": {
                "text": "<b>Welcome</b>",
                "parse_mode": "html",
                "version": 2,
                "legacy_markdown_text": "**Welcome**",
                "buttons": [],
                "legacy_buttons": [],
            },
            "security_note": {
                "text": "Prove you are human",
                "parse_mode": "html",
                "version": 2,
                "legacy_markdown_text": "Prove you are human\n[I am not a robot](btnwelcomesecurity)",
                # The captcha button is hard-added in code, so it is dropped here to avoid a duplicate.
                "buttons": [],
                "legacy_buttons": [],
            },
        }
    }


def test_convert_legacy_greetings_saveables_skips_modern_and_missing() -> None:
    migration = _legacy_greetings_migration()

    assert (
        migration.build_greetings_saveables_migration_update(
            {
                "_id": "greetings-id",
                "note": {"text": "<b>modern</b>", "parse_mode": "html", "version": 2},
                "security_note": None,
            }
        )
        is None
    )


def test_restore_legacy_greetings_saveables_restores_original() -> None:
    migration = _legacy_greetings_migration()

    update = migration.build_greetings_saveables_rollback_update(
        {
            "_id": "greetings-id",
            "note": {
                "text": "<b>Welcome</b>",
                "parse_mode": "html",
                "version": 2,
                "legacy_markdown_text": "**Welcome**",
                "buttons": [],
                "legacy_buttons": [],
            },
        }
    )

    assert update == {
        "$set": {
            "note": {
                "text": "**Welcome**",
                "parse_mode": "md",
                "version": 1,
                "buttons": [],
            }
        }
    }


@pytest.mark.asyncio
async def test_migration_module_imports() -> None:
    """Test that migration modules can be imported without errors."""
    migrations_dir = Path("sophie_bot/db/migrations")
    migration_files = sorted(migrations_dir.glob("[0-9]*.py"))

    for migration_file in migration_files:
        try:
            importlib.import_module(f"sophie_bot.db.migrations.{migration_file.stem}")
            print(f"✓ {migration_file.name}")
        except Exception as e:
            pytest.fail(f"Failed to import {migration_file.name}: {e}")


@pytest.mark.asyncio
async def test_migration_state_model_structure() -> None:
    """Test that MigrationState model is defined correctly."""
    from sophie_bot.db.models.migrations import MigrationState

    # Check that model is defined
    assert MigrationState is not None
    assert hasattr(MigrationState, "__name__")


def _filters_legacy_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260523_020000_convert_filters_legacy_actions")


def test_convert_filters_legacy_reply_message_action() -> None:
    migration = _filters_legacy_migration()

    update = migration.build_legacy_filter_migration_update(
        {
            "_id": "filter-id",
            "action": "reply_message",
            "actions": {},
            "reply_text": {"text": "hello", "parse_mode": "html"},
        }
    )

    assert update == {
        "$set": {
            "actions": {"reply": {"text": "hello", "parse_mode": "html"}},
            "action": None,
            "version": 2,
        },
        "$unset": {"reply_text": ""},
    }


def test_convert_filters_legacy_get_note_action() -> None:
    migration = _filters_legacy_migration()

    update = migration.build_legacy_filter_migration_update(
        {
            "_id": "filter-id",
            "action": "get_note",
            "actions": {},
            "note_name": "rules",
        }
    )

    assert update == {
        "$set": {
            "actions": {"send_note": {"notename": "rules"}},
            "action": None,
            "version": 2,
        },
        "$unset": {"note_name": ""},
    }


def test_convert_filters_legacy_delete_message_action() -> None:
    migration = _filters_legacy_migration()

    update = migration.build_legacy_filter_migration_update(
        {
            "_id": "filter-id",
            "action": "delete_message",
            "actions": {},
        }
    )

    assert update == {
        "$set": {
            "actions": {"delmsg": {}},
            "action": None,
            "version": 2,
        },
    }


def test_convert_filters_legacy_skips_modern_filters() -> None:
    migration = _filters_legacy_migration()

    assert (
        migration.build_legacy_filter_migration_update(
            {
                "action": "reply_message",
                "actions": {"reply": {"text": "already migrated"}},
            }
        )
        is None
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
