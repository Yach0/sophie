import importlib
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING
from beanie import Document

if TYPE_CHECKING:
    from beanie.migrations.controllers.base import BaseMigrationController

from sophie_bot.config import CONFIG
from sophie_bot.db.models.migrations import MigrationState
from sophie_bot.utils.logger import log


async def _get_migration_function(migration_class: type, direction: str = "forward") -> "BaseMigrationController":
    """
    Get the decorated migration function from a Forward or Backward class.

    Args:
        migration_class: The Forward or Backward class
        direction: 'forward' or 'backward' for logging

    Returns:
        The decorated migration function
    """
    from beanie.migrations.controllers.base import BaseMigrationController

    for attr_name in dir(migration_class):
        attr = getattr(migration_class, attr_name)
        if isinstance(attr, BaseMigrationController):
            return attr

    raise ValueError(f"No migration function found in {direction} class")


async def run_migrations() -> None:
    """
    Run all pending migrations automatically.
    """
    from sophie_bot.services.db import init_db

    await init_db(skip_indexes=True)

    if not CONFIG.run_migrations_on_startup:
        log.info("Migrations disabled by configuration")
        return

    migrations_path = Path(CONFIG.migrations_path)
    if not migrations_path.exists():
        log.warning(f"Migrations directory not found: {CONFIG.migrations_path}")
        return

    # Get all migration files sorted alphabetically
    migration_files = sorted(migrations_path.glob("[0-9]*.py"))

    if not migration_files:
        log.info("No migration files found")
        return

    # Get list of already applied migrations
    applied_migrations = {m.name: m for m in await MigrationState.find_all().to_list()}

    log.info("Migration check started", total_files=len(migration_files), already_applied=len(applied_migrations))

    migrations_to_run = []
    for migration_file in migration_files:
        module_name = migration_file.stem

        if module_name in applied_migrations:
            log.debug("Migration already applied", migration=module_name)
            continue

        migrations_to_run.append((module_name, migration_file))

    if not migrations_to_run:
        log.info("All migrations are up to date")
        return

    log.info("Starting migrations", count=len(migrations_to_run), mode=CONFIG.migration_mode)

    # Run migrations in sequence
    for module_name, migration_file in migrations_to_run:
        await _run_single_migration(module_name)

    log.info("All migrations completed successfully")


async def _run_migration_action(module_name: str, direction: str = "forward") -> None:
    """
    Execute a migration in the specified direction (forward/backward).

    Args:
        module_name: The migration module name (e.g., "20240125_120000_add_field")
        direction: "forward" (apply) or "backward" (rollback)
    """
    from sophie_bot.services.db import init_db, db, async_mongo

    # Ensure DB is initialized (idempotent)
    await init_db(skip_indexes=True)

    log_ctx = log.bind(migration=module_name, direction=direction)
    log_ctx.info(f"Starting {direction} migration")

    start_time = time.time()

    try:
        # Import migration module
        try:
            module = importlib.import_module(f"sophie_bot.db.migrations.{module_name}")
        except ImportError as e:
            log_ctx.error("Failed to import migration module", error=str(e))
            raise

        # Determine class and method based on direction
        class_name = "Forward" if direction == "forward" else "Backward"

        if not hasattr(module, class_name):
            error_msg = f"Migration {module_name} must have a {class_name} class"
            log_ctx.error(f"Migration missing {class_name} class")
            raise ValueError(error_msg)

        migration_class = getattr(module, class_name)
        migration_func = await _get_migration_function(migration_class, direction)

        # Initialize specific models required by this migration
        models_to_init: list[type[Document]] = []

        for attr in ("document_models", "input_document_model", "output_document_model"):
            if hasattr(migration_func, attr) and (val := getattr(migration_func, attr)):
                if isinstance(val, list):
                    models_to_init.extend(val)
                else:
                    models_to_init.append(val)

        if models_to_init:
            from beanie import init_beanie
            from sophie_bot.db.models import models

            # Re-init beanie with specific models for this migration + all existing models
            # Note: We use list(set(...)) to remove duplicates
            await init_beanie(
                database=db,
                document_models=list(set(models + models_to_init)),
                skip_indexes=True,
            )

        # Execute the migration
        if CONFIG.migration_use_transactions and CONFIG.mongo_use_replica_set:
            async with async_mongo.start_session() as session:
                async with await session.start_transaction():
                    await migration_func.run(session=session)
        else:
            await migration_func.run(session=None)

        # Update MigrationState
        duration_ms = int((time.time() - start_time) * 1000)

        if direction == "forward":
            migration_state = MigrationState(
                name=module_name,
                version="1.0",
                batch_size=None,
                duration_ms=duration_ms,
            )
            await migration_state.insert()
        else:
            # For rollback, remove the state record
            await MigrationState.find_one(MigrationState.name == module_name).delete()

        log_ctx.info(f"{direction.capitalize()} migration completed successfully", duration_ms=duration_ms)

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        log_ctx.error(f"{direction.capitalize()} migration failed", error=str(e), duration_ms=duration_ms)
        raise


async def _run_single_migration(module_name: str) -> None:
    """Wrapper for forward migration to maintain compatibility."""
    await _run_migration_action(module_name, direction="forward")


async def run_migration_backward(module_name: str) -> None:
    """Wrapper for backward migration to maintain compatibility."""
    await _run_migration_action(module_name, direction="backward")


async def run_all_migrations_backward() -> None:
    """
    Rollback all applied migrations in reverse order.
    """
    from sophie_bot.services.db import init_db

    await init_db(skip_indexes=True)

    applied_states = await MigrationState.find_all().to_list()

    if not applied_states:
        log.info("No migrations to rollback")
        return

    # Sort migrations by name in reverse order (newest first)
    applied_states.sort(key=lambda x: x.name, reverse=True)

    log.info("Starting rollback of all migrations", count=len(applied_states))

    for state in applied_states:
        await _run_migration_action(state.name, direction="backward")

    log.info("All migrations rolled back successfully")


async def get_migration_status() -> dict[str, Any]:
    """
    Get the current migration status.

    Returns:
        Dictionary with migration status information
    """
    from sophie_bot.services.db import init_db

    await init_db(skip_indexes=True)

    migrations_path = Path(CONFIG.migrations_path)

    if not migrations_path.exists():
        return {
            "status": "no_migrations_directory",
            "total": 0,
            "applied": 0,
            "pending": 0,
            "applied_migrations": [],
            "pending_migrations": [],
        }

    migration_files = sorted(migrations_path.glob("[0-9]*.py"))
    applied_states = await MigrationState.find_all().to_list()
    applied_names = {state.name for state in applied_states}

    pending = []
    for migration_file in migration_files:
        module_name = migration_file.stem
        if module_name not in applied_names:
            pending.append(module_name)

    return {
        "status": "ok",
        "total": len(migration_files),
        "applied": len(applied_states),
        "pending": len(pending),
        "applied_migrations": [state.name for state in applied_states],
        "pending_migrations": pending,
        "migrations_path": str(migrations_path),
    }
