from __future__ import annotations

import copy
import importlib
import inspect
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from beanie import Document, init_beanie
from redis.asyncio import Redis

from sophie_bot.config import CONFIG
from sophie_bot.db.models import models
from sophie_bot.db.models.migrations import MigrationState
from sophie_bot.services.db import DatabaseResources
from sophie_bot.utils.logger import log

if TYPE_CHECKING:
    from beanie.migrations.controllers.base import BaseMigrationController


@dataclass(frozen=True, slots=True)
class MigrationResources:
    database: DatabaseResources
    redis: Redis


async def _get_migration_function(
    migration_class: type,
    direction: str = "forward",
) -> BaseMigrationController:
    from beanie.migrations.controllers.base import BaseMigrationController

    for attribute_name in dir(migration_class):
        attribute = getattr(migration_class, attribute_name)
        if isinstance(attribute, BaseMigrationController):
            return attribute
    raise ValueError(f"No migration function found in {direction} class")


def _bind_migration_resources(
    controller: BaseMigrationController,
    resources: MigrationResources,
) -> BaseMigrationController:
    function_signature = inspect.signature(controller.function)
    if "resources" not in function_signature.parameters:
        return controller
    bound_controller = copy.copy(controller)
    bound_controller.function = partial(controller.function, resources=resources)
    cast(Any, bound_controller).function_signature = inspect.signature(bound_controller.function)
    return bound_controller


async def run_migrations(resources: MigrationResources) -> None:
    migrations_path = Path(CONFIG.migrations_path)
    if not migrations_path.exists():
        log.warning(f"Migrations directory not found: {CONFIG.migrations_path}")
        return

    migration_files = sorted(migrations_path.glob("[0-9]*.py"))
    if not migration_files:
        log.info("No migration files found")
        return

    applied_migrations = {migration.name: migration for migration in await MigrationState.find_all().to_list()}
    migrations_to_run = [
        migration_file.stem for migration_file in migration_files if migration_file.stem not in applied_migrations
    ]
    if not migrations_to_run:
        log.info("All migrations are up to date")
        return

    log.info("Starting migrations", count=len(migrations_to_run), mode=CONFIG.migration_mode)
    for module_name in migrations_to_run:
        await _run_single_migration(module_name, resources)
    log.info("All migrations completed successfully")


async def _run_migration_action(
    module_name: str,
    resources: MigrationResources,
    direction: str = "forward",
) -> None:
    log_context = log.bind(migration=module_name, direction=direction)
    log_context.info(f"Starting {direction} migration")
    start_time = time.time()
    try:
        module = importlib.import_module(f"sophie_bot.db.migrations.{module_name}")
        class_name = "Forward" if direction == "forward" else "Backward"
        if not hasattr(module, class_name):
            raise ValueError(f"Migration {module_name} must have a {class_name} class")

        migration_class = getattr(module, class_name)
        original_controller = await _get_migration_function(migration_class, direction)
        migration_controller = _bind_migration_resources(original_controller, resources)

        models_to_init: list[type[Document]] = []
        for attribute_name in ("document_models", "input_document_model", "output_document_model"):
            if value := getattr(migration_controller, attribute_name, None):
                if isinstance(value, list):
                    models_to_init.extend(value)
                else:
                    models_to_init.append(value)
        if models_to_init:
            await init_beanie(
                database=resources.database.database,
                document_models=list(set(models + models_to_init)),
                skip_indexes=True,
            )

        if CONFIG.migration_use_transactions and CONFIG.mongo_use_replica_set:
            async with resources.database.mongo.start_session() as session, await session.start_transaction():
                await migration_controller.run(session=session)
        else:
            await migration_controller.run(session=None)

        duration_ms = int((time.time() - start_time) * 1000)
        if direction == "forward":
            await MigrationState(
                name=module_name,
                version="1.0",
                batch_size=None,
                duration_ms=duration_ms,
            ).insert()
        else:
            migration_state = await MigrationState.find_one(MigrationState.name == module_name)
            if migration_state is not None:
                await migration_state.delete()
        log_context.info(
            f"{direction.capitalize()} migration completed successfully",
            duration_ms=duration_ms,
        )
    except Exception as error:
        duration_ms = int((time.time() - start_time) * 1000)
        log_context.error(
            f"{direction.capitalize()} migration failed",
            error=str(error),
            duration_ms=duration_ms,
        )
        raise


async def _run_single_migration(module_name: str, resources: MigrationResources) -> None:
    await _run_migration_action(module_name, resources, direction="forward")


async def run_migration_backward(module_name: str, resources: MigrationResources) -> None:
    await _run_migration_action(module_name, resources, direction="backward")


async def run_all_migrations_backward(resources: MigrationResources) -> None:
    applied_states = await MigrationState.find_all().to_list()
    if not applied_states:
        log.info("No migrations to rollback")
        return
    applied_states.sort(key=lambda migration_state: migration_state.name, reverse=True)
    for state in applied_states:
        await _run_migration_action(state.name, resources, direction="backward")
    log.info("All migrations rolled back successfully")


async def get_migration_status() -> dict[str, Any]:
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
    pending = [file.stem for file in migration_files if file.stem not in applied_names]
    return {
        "status": "ok",
        "total": len(migration_files),
        "applied": len(applied_states),
        "pending": len(pending),
        "applied_migrations": [state.name for state in applied_states],
        "pending_migrations": pending,
        "migrations_path": str(migrations_path),
    }
