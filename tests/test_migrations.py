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
        },
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
