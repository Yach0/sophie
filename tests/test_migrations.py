"""Test suite for database migrations."""

import pytest


@pytest.mark.asyncio
async def test_migration_module_imports():
    """Test that migration modules can be imported without errors."""
    import importlib
    from pathlib import Path

    migrations_dir = Path("sophie_bot/db/migrations")
    migration_files = sorted(migrations_dir.glob("[0-9]*.py"))

    for migration_file in migration_files:
        try:
            importlib.import_module(f"sophie_bot.db.migrations.{migration_file.stem}")
            print(f"✓ {migration_file.name}")
        except Exception as e:
            pytest.fail(f"Failed to import {migration_file.name}: {e}")


@pytest.mark.asyncio
async def test_migration_state_model_structure():
    """Test that MigrationState model is defined correctly."""
    from sophie_bot.db.models.migrations import MigrationState

    # Check that model is defined
    assert MigrationState is not None
    assert hasattr(MigrationState, "__name__")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
