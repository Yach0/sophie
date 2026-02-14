#!/usr/bin/env python3
"""Helper script for creating Beanie migrations."""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path to allow importing sophie_bot
sys.path.append(str(Path(__file__).parent.parent))


MIGRATION_TEMPLATE = '''"""Migration: {name}

Description:
    <Add description here>

Affected Collections:
    - <list affected collections>

Impact:
    - Low/Medium/High risk
    - Small/Medium/Large collection
    - <Additional notes>
"""

from __future__ import annotations

from beanie import Document, iterative_migration


class Forward:
    """<Description of forward migration>"""
    
    @iterative_migration()
    async def migrate(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        """
        Apply migration to a single document.
        
        Args:
            input_document: Original document structure
            output_document: New document structure
        """
        # Add migration logic here
        pass


class Backward:
    """<Description of backward migration>"""
    
    @iterative_migration()
    async def rollback(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        """
        Rollback migration for a single document.
        
        Args:
            input_document: New document structure
            output_document: Original document structure
        """
        # Add rollback logic here
        pass
'''


def create_migration(name: str, path: str = "sophie_bot/db/migrations") -> None:
    """
    Create a new migration file.

    Args:
        name: Migration name (e.g., "add_user_preferences")
        path: Path to migrations directory
    """
    migrations_path = Path(path)

    if not migrations_path.exists():
        print(f"Error: Migrations directory not found: {migrations_path}")
        return

    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{name}.py"
    filepath = migrations_path / filename

    # Check if file already exists
    if filepath.exists():
        print(f"Error: Migration file already exists: {filepath}")
        return

    # Create migration file
    filepath.write_text(MIGRATION_TEMPLATE.format(name=name))

    print(f"✓ Created migration: {filepath}")
    print("\nNext steps:")
    print("  1. Edit the migration file to implement Forward and Backward logic")
    print("  2. Test the migration: make migrate_up")
    print("  3. Check status: make migrate_status")
    print("  4. Add tests to tests/test_migrations.py")


def list_migrations(path: str = "sophie_bot/db/migrations") -> None:
    """
    List all migrations.

    Args:
        path: Path to migrations directory
    """
    migrations_path = Path(path)

    if not migrations_path.exists():
        print(f"Error: Migrations directory not found: {migrations_path}")
        return

    migration_files = sorted(migrations_path.glob("[0-9]*.py"))

    if not migration_files:
        print("No migrations found")
        return

    print(f"Found {len(migration_files)} migration(s):")
    print()

    for migration_file in migration_files:
        print(f"  • {migration_file.name}")


def validate_migration(path: str) -> None:
    """
    Validate a migration file.

    Args:
        path: Path to migration file
    """
    migration_path = Path(path)

    if not migration_path.exists():
        print(f"Error: Migration file not found: {migration_path}")
        return

    # Try to import the migration
    import importlib.util

    spec = importlib.util.spec_from_file_location(migration_path.stem, migration_path)

    if spec is None or spec.loader is None:
        print(f"Error: Could not load migration file: {migration_path}")
        return

    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        print("✗ Migration has syntax or import errors:")
        print(f"  {e}")
        return

    # Check for required classes
    if not hasattr(module, "Forward"):
        print("✗ Migration missing Forward class")
        return

    if not hasattr(module, "Backward"):
        print("✗ Migration missing Backward class")
        return

    # Check Forward class
    from beanie.migrations.controllers.base import BaseMigrationController

    forward_class = module.Forward
    has_migration_func = False

    for attr_name in dir(forward_class):
        attr = getattr(forward_class, attr_name)
        if isinstance(attr, BaseMigrationController):
            has_migration_func = True
            print(f"✓ Forward migration found: {attr_name}")
            break

    if not has_migration_func:
        print("✗ Forward class missing migration function")
        return

    # Check Backward class
    backward_class = module.Backward
    has_migration_func = False

    for attr_name in dir(backward_class):
        attr = getattr(backward_class, attr_name)
        if isinstance(attr, BaseMigrationController):
            has_migration_func = True
            print(f"✓ Backward migration found: {attr_name}")
            break

    if not has_migration_func:
        print("✗ Backward class missing migration function")
        return

    print(f"✓ Migration {migration_path.name} is valid")


async def run_migrations_up() -> None:
    """Run all pending migrations."""
    # Set environment variable to force migration run
    os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "true"

    try:
        from sophie_bot.services.migrations import run_migrations

        await run_migrations()
    except Exception as e:
        print(f"Error running migrations: {e}")
        sys.exit(1)


async def run_single_migration(migration_name: str) -> None:
    """
    Run a specific migration by name.

    Args:
        migration_name: Name of the migration to run
    """
    # Set environment variable to force migration run
    os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "true"

    try:
        from sophie_bot.services.migrations import _run_single_migration

        await _run_single_migration(migration_name)
    except Exception as e:
        print(f"Error running migration: {e}")
        sys.exit(1)


async def run_migration_down(migration_name: str) -> None:
    """
    Rollback a specific migration.

    Args:
        migration_name: Name of the migration to rollback
    """
    # Set environment variable to force migration run
    os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "true"

    try:
        from sophie_bot.services.migrations import run_migration_backward

        await run_migration_backward(migration_name)
    except Exception as e:
        print(f"Error rolling back migration: {e}")
        sys.exit(1)


async def run_all_migrations_down() -> None:
    """
    Rollback all applied migrations.
    """
    # Set environment variable to force migration run
    os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "true"

    try:
        from sophie_bot.services.migrations import run_all_migrations_backward

        await run_all_migrations_backward()
    except Exception as e:
        print(f"Error rolling back all migrations: {e}")
        sys.exit(1)


async def show_migration_status() -> None:
    """Show status of all migrations."""
    try:
        from sophie_bot.services.migrations import get_migration_status

        status = await get_migration_status()
        print(json.dumps(status, indent=2))
    except Exception as e:
        print(f"Error getting migration status: {e}")
        sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Beanie migration helper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a new migration
  %(prog)s create add_user_preferences

  # Run pending migrations
  %(prog)s up

  # Run a specific migration
  %(prog)s run 20240125_120000_add_field

  # Rollback a specific migration
  %(prog)s down 20240125_120000_add_field

  # Rollback all migrations
  %(prog)s down_all

  # Show migration status
  %(prog)s status
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new migration")
    create_parser.add_argument("name", help="Migration name")
    create_parser.add_argument(
        "-p",
        "--path",
        default="sophie_bot/db/migrations",
        help="Path to migrations directory (default: sophie_bot/db/migrations)",
    )

    # List command
    list_parser = subparsers.add_parser("list", help="List all migrations")
    list_parser.add_argument(
        "-p",
        "--path",
        default="sophie_bot/db/migrations",
        help="Path to migrations directory (default: sophie_bot/db/migrations)",
    )

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate a migration file")
    validate_parser.add_argument("path", help="Path to migration file to validate")

    # Up command
    subparsers.add_parser("up", help="Run pending migrations")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a specific migration")
    run_parser.add_argument("migration", help="Migration name to run")

    # Down command
    down_parser = subparsers.add_parser("down", help="Rollback a migration")
    down_parser.add_argument("migration", help="Migration name to rollback")

    # Down all command
    subparsers.add_parser("down_all", help="Rollback all migrations")

    # Status command
    subparsers.add_parser("status", help="Show migration status")

    args = parser.parse_args()

    if args.command == "create":
        create_migration(args.name, args.path)
    elif args.command == "list":
        list_migrations(args.path)
    elif args.command == "validate":
        validate_migration(args.path)
    elif args.command == "up":
        asyncio.run(run_migrations_up())
    elif args.command == "run":
        asyncio.run(run_single_migration(args.migration))
    elif args.command == "down":
        asyncio.run(run_migration_down(args.migration))
    elif args.command == "down_all":
        asyncio.run(run_all_migrations_down())
    elif args.command == "status":
        asyncio.run(show_migration_status())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
