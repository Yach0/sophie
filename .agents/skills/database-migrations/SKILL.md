---
name: database-migrations
description: Use this skill when creating or changing Beanie migrations, moving stored data, or evolving persisted schemas in Sophie Bot.
---

# Database migrations

Use this skill for changes under `sophie_bot/db/migrations/` and for any task that changes persisted MongoDB documents.

## Create migrations the project way

- Prefer `make new_migration NAME=<descriptive_name>`.
- Equivalent helper: `uv run python tools/migration_helper.py create <descriptive_name>`.
- Use descriptive names such as `add_user_preferences`, not generic numeric names.

## Required file structure

Every migration should include:

1. A docstring describing the goal, affected collections, and impact.
2. A `Forward` class.
3. A `Backward` class.

Backward logic is required. Do not ship one-way migrations unless the task explicitly accepts that risk.

## Choosing the migration style

- Use `@iterative_migration()` for document-by-document changes.
- Use `@free_fall_migration()` when you need session control, bulk operations, batching, or special sequencing.
- For very large collections, prefer batched work and keep transaction limits in mind.

## Safety checklist

- Keep migrations idempotent when possible.
- Consider batch size, runtime, and rollback behavior before writing code.
- For replica-set deployments, transaction support can be enabled, but do not assume it is always available.
- Large migrations should be staged and observed carefully.

## Verification workflow

- Run `make migrate_up` locally when the environment supports it.
- Check status with `make migrate_status`.
- Verify rollback logic before considering the migration done.
- Add or update migration tests when the project already covers that area.

## Useful references

- `sophie_bot/db/migrations/`
- `tools/migration_helper.py`
- `config.py`