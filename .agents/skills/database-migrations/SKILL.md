---
name: database-migrations
description: Use when creating or modifying Beanie migrations, moving stored data, or evolving schemas.
---

# Database Migrations

## Creation & Structure

- Create via `make new_migration NAME=<name>` or `uv run python tools/migration_helper.py create <name>`.
- Structure requires: docstring describing impact, a `Forward` class, and a `Backward` class.
- Always implement rollback logic; avoid irreversible one-way migrations unless explicitly accepted.

## Migration Design

- Use `@iterative_migration()` for document-by-document updates.
- Use `@free_fall_migration()` for bulk operations, raw collection queries, or custom session sequencing.
- Ensure operations are idempotent. Batch heavy operations to respect memory and transaction limits.

## Verification

- Test execution via `make migrate_up` and check status via `make migrate_status`.
- Test backward rollback before completion. Add corresponding migration unit tests.
