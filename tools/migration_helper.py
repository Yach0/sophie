#!/usr/bin/env python3
"""Helper script for creating Beanie migrations."""

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from beanie.migrations.controllers.base import BaseMigrationController

# Add project root to path to allow importing sophie_bot
sys.path.append(str(Path(__file__).parent.parent))

from sophie_bot.config import CONFIG
from sophie_bot.services.db import init_db, open_database
from sophie_bot.services.migrations import (
    MigrationResources,
    _run_single_migration,
    get_migration_status,
    run_all_migrations_backward,
    run_migration_backward,
    run_migrations,
)
from sophie_bot.services.redis import create_redis

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
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
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
    spec = importlib.util.spec_from_file_location(migration_path.stem, migration_path)

    if spec is None or spec.loader is None:
        print(f"Error: Could not load migration file: {migration_path}")
        return

    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:  # noqa: BLE001  # CLI boundary: report any load failure and abort
        print("✗ Migration has syntax or import errors:")
        print(f"  {error}")
        return

    # Check for required classes
    if not hasattr(module, "Forward"):
        print("✗ Migration missing Forward class")
        return

    if not hasattr(module, "Backward"):
        print("✗ Migration missing Backward class")
        return

    # Check Forward class
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


async def run_database_command(args: argparse.Namespace) -> None:
    """Own the clients and Beanie binding for a database-backed CLI command."""
    try:
        async with open_database(CONFIG) as database:
            redis = create_redis(CONFIG)
            try:
                await init_db(database.database, config=CONFIG, skip_indexes=True)
                resources = MigrationResources(database=database, redis=redis)
                match args.command:
                    case "up":
                        await run_migrations(resources)
                    case "run":
                        await _run_single_migration(args.migration, resources)
                    case "down":
                        await run_migration_backward(args.migration, resources)
                    case "down_all":
                        await run_all_migrations_backward(resources)
                    case "status":
                        status = await get_migration_status(resources)
                        print(json.dumps(status, indent=2))
            finally:
                await redis.aclose()
    except Exception as error:  # CLI boundary: report failures after releasing clients
        print(f"Error running migration command '{args.command}': {error}")
        raise SystemExit(1) from error


def main() -> None:
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
    elif args.command in {"up", "run", "down", "down_all", "status"}:
        asyncio.run(run_database_command(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
