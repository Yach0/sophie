---
icon: 🗃️
title: Database Migrations
---

This guide covers Sophie Bot's database migration system using Beanie ODM.

## Overview

Sophie uses Beanie's built-in migration system to manage database schema evolution.
Migrations are versioned, reversible Python scripts that transform data between schema versions.

## How It Works

1. Migration files are stored in `sophie_bot/db/migrations/`
2. Each migration has a timestamp prefix: `YYYYMMDD_HHMMSS_description.py`
3. Migrations define `Forward` and `Backward` classes for apply/rollback
4. Applied migrations are tracked in the `migration_states` collection
5. Migrations run automatically on startup for beta instance

## Creating a Migration

### Quick Start

```bash
make new_migration NAME=add_user_preferences
```

This creates a file like: `sophie_bot/db/migrations/20240125_120000_add_user_preferences.py`

### Manual Creation

1. Create file: `sophie_bot/db/migrations/20240125_120000_add_user_preferences.py`
2. Define old and new model structures if needed
3. Implement Forward and Backward migration classes
4. Test migration locally
5. Add tests to `tests/test_migrations.py`

## Migration Examples

### Example 1: Add New Field

```python
"""Migration: Add user preferences

Description:
    Adds user preferences to support theme and notification settings.

Affected Collections:
    - chats

Impact:
    - Low risk: Only adds optional field with defaults
    - Large collection: Chats can have millions of documents
    - Backward compatible: Code can check for field existence
"""

from __future__ import annotations

from beanie import Document, iterative_migration


class Forward:
    """Add preferences field to chats."""
    
    @iterative_migration()
    async def add_preferences(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        output_document.preferences = {
            "theme": "light",
            "notifications": True
        }


class Backward:
    """Remove preferences field."""
    
    @iterative_migration()
    async def remove_preferences(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        # Field will be removed automatically
        pass
```

### Example 2: Rename Field

```python
"""Migration: Rename chat_id to chat

Description:
    Renames chat_id field to chat to match new naming convention.

Affected Collections:
    - notes

Impact:
    - Medium risk: Field rename requires code changes
    - Medium collection size
"""

from __future__ import annotations

from beanie import Document, iterative_migration


class Forward:
    @iterative_migration()
    async def rename_field(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        output_document.chat = input_document.chat_id


class Backward:
    @iterative_migration()
    async def revert_rename(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        output_document.chat_id = input_document.chat
```

### Example 3: Convert Data Type

```python
"""Migration: Convert timestamp string to datetime

Description:
    Converts timestamp strings to datetime objects for proper querying.

Affected Collections:
    - logs

Impact:
    - Low risk: Type conversion only
    - Large collection: Can have millions of entries
"""

from __future__ import annotations

from datetime import datetime
from beanie import Document, iterative_migration


class Forward:
    @iterative_migration()
    async def convert_timestamp(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        if isinstance(input_document.created_at, str):
            output_document.created_at = datetime.fromisoformat(
                input_document.created_at
            )


class Backward:
    @iterative_migration()
    async def revert_timestamp(
        self, 
        input_document: Document, 
        output_document: Document
    ):
        if isinstance(input_document.created_at, datetime):
            output_document.created_at = input_document.created_at.isoformat()
```

### Example 4: Bulk Migration with Free Fall

```python
"""Migration: Index optimization

Description:
    Rebuilds indexes for large collection using free fall migration.

Affected Collections:
    - messages

Impact:
    - Medium risk: Index rebuild operation
    - Very large collection: Millions of messages
    - Requires: Maintenance window recommended
"""

from __future__ import annotations

from beanie import Document, free_fall_migration


class Forward:
    @free_fall_migration(document_models=["messages"])
    async def rebuild_indexes(self, session):
        """Rebuild all indexes with better configuration."""
        # Get collection
        from sophie_bot.db.models import MessageModel
        collection = MessageModel.get_motor_collection()
        
        # Drop old indexes
        await collection.drop_indexes()
        
        # Create new optimized indexes
        await collection.create_index([("chat_id", 1), ("created_at", -1)])
        await collection.create_index([("user_id", 1)])


class Backward:
    @free_fall_migration(document_models=["messages"])
    async def restore_indexes(self, session):
        """Restore original index configuration."""
        from sophie_bot.db.models import MessageModel
        collection = MessageModel.get_motor_collection()
        
        # Drop new indexes
        await collection.drop_indexes()
        
        # Restore original indexes
        await collection.create_index([("chat_id", 1)])
        await collection.create_index([("user_id", 1)])
```

## Migration Types

### Iterative Migration

**When to use:**
- Most document transformations
- When you need to transform data field-by-field
- Simple field additions, renames, or type conversions

**Pros:**
- Beanie handles document loading and saving
- Memory efficient (processes one document at a time)
- Can be resumed if interrupted
- Good for large collections

**Cons:**
- Limited control over database session
- Can be slower for bulk operations
- Hard to track progress

### Free Fall Migration

**When to use:**
- Bulk operations (rebuilding indexes, collections)
- When you need full database control
- Complex transformations requiring multiple collections
- Performance-critical operations

**Pros:**
- Full control over database session
- Can use batch processing
- Better performance for bulk operations
- Can track progress manually

**Cons:**
- More complex to implement
- Must handle document loading/saving manually
- Risk of memory issues with large collections

## Running Migrations

### Automatic (Recommended for Beta)

Migrations run automatically on startup when `instance_name=beta`:

```python
# In config
instance_name: "beta"
run_migrations_on_startup: true
```

### Manual

Run all pending migrations:

```bash
make migrate_up
```

Or using Python directly:

```python
import asyncio
from sophie_bot.services.migrations import run_migrations

asyncio.run(run_migrations())
```

### Check Status

```bash
make migrate_status
```

Example output:

```json
{
  "status": "ok",
  "total": 4,
  "applied": 2,
  "pending": 2,
  "applied_migrations": [
    "20240125_001_add_chat_link_to_notes",
    "20240125_002_convert_antiflood_legacy_actions"
  ],
  "pending_migrations": [
    "20240125_003_convert_connections_links",
    "20240125_004_test_migration"
  ]
}
```

### Rollback

Rollback a specific migration:

```bash
make migrate_rollback MIGRATION=20240125_001_add_field
```

Or using Python:

```python
import asyncio
from sophie_bot.services.migrations import run_migration_backward

asyncio.run(run_migration_backward("20240125_001_add_field"))
```

## Configuration

Configure migrations in `config.py`:

```python
class Config(BaseSettings):
    # Migration configuration
    run_migrations_on_startup: bool = True
    migrations_path: str = "sophie_bot/db/migrations"
    migration_mode: Literal["auto", "manual"] = "auto"
    migration_use_transactions: bool = False  # Requires replica set
    migration_batch_size: int = 1000  # For large collections
    migration_timeout_seconds: int = 3600  # 1 hour timeout per migration
    
    # MongoDB settings for transactions
    mongo_use_replica_set: bool = False  # Set to True for transactions
```

## Transaction Support

MongoDB transactions provide atomicity for migrations, ensuring either all changes succeed or none do.

### Enabling Transactions

1. **Configure MongoDB replica set** (required for transactions)
2. **Enable replica set in config**:

```python
mongo_use_replica_set: True
migration_use_transactions: True
```

### When to Use Transactions

**Use transactions for:**
- Multi-collection migrations
- Critical data transformations
- Rollback safety is essential
- Collections with relational dependencies

**Avoid transactions for:**
- Very large collections (millions of documents)
- Simple field additions
- Non-critical migrations
- When replica set is not available

### Transaction Limitations

MongoDB transactions have limits:
- **Document size**: 16MB per document
- **Transaction size**: 16MB total
- **Operation count**: Varies by MongoDB version
- **Execution time**: 60 seconds default

For large collections, use free fall migration without transactions.

## Handling Large Collections

### Batching Strategy

For collections with millions of documents, implement batching:

```python
from beanie import Document, free_fall_migration

class Forward:
    @free_fall_migration(document_models=[OldModel, NewModel])
    async def migrate(self, session):
        batch = []
        batch_size = CONFIG.migration_batch_size  # e.g., 1000
        
        async for old_doc in OldModel.find_all():
            batch.append(OldModel(**old_doc.model_dump()))
            
            # Process batch when full
            if len(batch) >= batch_size:
                for doc in batch:
                    await doc.save(session=session)
                batch = []
                print(f"Processed {batch_size} documents...")
        
        # Process remaining documents
        for doc in batch:
            await doc.save(session=session)
```

### Progress Monitoring

Add progress logging for long-running migrations:

```python
class Forward:
    @free_fall_migration(document_models=[OldModel])
    async def migrate(self, session):
        total = await OldModel.count()
        processed = 0
        
        async for doc in OldModel.find_all():
            # Process document
            await self._process_document(doc, session)
            
            processed += 1
            if processed % 10000 == 0:
                percent = (processed / total) * 100
                print(f"Progress: {processed}/{total} ({percent:.1f}%)")
```

### Performance Tips

1. **Use appropriate batch size**: 1000-10000 typically works well
2. **Create indexes before migration**: Improves query performance
3. **Drop indexes before bulk inserts**: Faster for large insertions
4. **Monitor memory usage**: Adjust batch size if memory issues occur
5. **Run during maintenance**: Reduces impact on users

## Testing

### Running Tests

```bash
make test_migrations
```

Or:

```bash
pytest tests/test_migrations.py -v
```

### Writing Tests

Add tests to `tests/test_migrations.py`:

```python
import pytest
from sophie_bot.services.migrations import run_migrations, run_migration_backward
from sophie_bot.db.models.migrations import MigrationState


@pytest.mark.asyncio
async def test_migration_applies_correctly():
    """Test that migration applies and can be rolled back."""
    # Run migration
    await run_migrations()
    
    # Verify migration was applied
    state = await MigrationState.find_one(
        MigrationState.name == "20240125_001_test_migration"
    )
    assert state is not None
    
    # Rollback
    await run_migration_backward("20240125_001_test_migration")
    
    # Verify rollback
    state = await MigrationState.find_one(
        MigrationState.name == "20240125_001_test_migration"
    )
    assert state is None
```

## Deployment Workflow

1. **Create migration** on development branch
2. **Test locally**: `make migrate_up`
3. **Add tests** to `tests/test_migrations.py`
4. **Test in staging** (beta instance)
5. **Verify status**: `make migrate_status`
6. **Open merge request** with migration details
7. **CI runs tests** automatically
8. **Merge to main**
9. **Deploy to beta** - migrations run automatically
10. **Verify in beta**
11. **Deploy to stable** if needed

## Troubleshooting

### Migration Failed Mid-Execution

**Symptoms:**
- Migration error in logs
- Some documents migrated, some not
- Migration state may or may not be recorded

**Solutions:**
1. Check logs for specific error details
2. Manually fix affected documents if needed
3. Re-run migration - it will skip already-applied steps
4. Consider rolling back if state is inconsistent

```bash
# Check status
make migrate_status

# Re-run migration (idempotent)
make migrate_up

# Rollback if needed
make migrate_rollback MIGRATION=<name>
```

### Migration Too Slow

**Symptoms:**
- Migration taking hours to complete
- High CPU/memory usage
- Database slow to respond

**Solutions:**
1. Use `@free_fall_migration()` for better control
2. Implement batching with `migration_batch_size`
3. Create appropriate indexes before migration
4. Consider running during maintenance window
5. Monitor and optimize database performance

```python
# Use free fall with batching
CONFIG.migration_batch_size = 5000  # Adjust based on testing
```

### Need to Roll Back

**Procedure:**

1. **Stop application** to prevent conflicts
2. **Identify migration** to rollback:
   ```bash
   make migrate_status
   ```
3. **Run rollback**:
   ```bash
   make migrate_rollback MIGRATION=20240125_001_add_field
   ```
4. **Verify rollback** with status check
5. **Fix migration code** if issue was in migration
6. **Re-apply migration** if needed

**Important:**
- Rollbacks modify data - test in staging first
- Rollbacks may take time for large collections
- Monitor logs during rollback
- Have data backup if possible

### Transaction Errors

**Symptoms:**
- `Transaction numbers do not match` error
- `Transaction is not in progress` error
- Migration fails with transaction-related error

**Solutions:**
1. Check MongoDB is running as replica set
2. Verify `mongo_use_replica_set = True` in config
3. Disable transactions for large collections:
   ```python
   migration_use_transactions = False
   ```
4. Use free fall migration instead

## Best Practices

### Before Creating Migration

- [ ] Document migration purpose and expected changes
- [ ] Identify affected models and collections
- [ ] Estimate document count for affected collections
- [ ] Plan both Forward and Backward logic
- [ ] Consider impact on running application
- [ ] Add tests for migration
- [ ] Update documentation
- [ ] Get code review

### Before Deploying to Production

- [ ] Verify migration passed in CI
- [ ] Test in staging environment
- [ ] Confirm data integrity after migration
- [ ] Document rollback procedure
- [ ] Notify team of deployment window
- [ ] Monitor logs after deployment
- [ ] Verify application functionality

### Common Pitfalls

**❌ Don't:**
- Create migrations without backward classes
- Skip testing migrations
- Run migrations on production without testing
- Use broad exception handlers that hide errors
- Forget to document migration impact
- Run migrations on stable instance (use beta only)

**✅ Do:**
- Implement both Forward and Backward classes
- Test migrations thoroughly in staging
- Use descriptive migration names
- Handle large collections with batching
- Monitor migration progress with logs
- Document rollback procedures

## References

- [Beanie Migration Documentation](https://beanie-odm.dev/tutorial/migrations/)
- [Project AGENTS.md](../AGENTS.md)
- [MongoDB Transactions](https://www.mongodb.com/docs/manual/core/transactions/)
- [Beanie ODM Documentation](https://beanie-odm.dev/)

## Migration Checklist

**Pre-Migration:**
- [ ] Migration file created with proper naming
- [ ] Forward and Backward classes implemented
- [ ] Docstring with description and impact
- [ ] Tests written in `tests/test_migrations.py`
- [ ] Code reviewed by team
- [ ] Tested locally with `make migrate_up`
- [ ] Tested in staging environment

**Post-Migration:**
- [ ] Migration applied successfully
- [ ] Data integrity verified
- [ ] Application functionality tested
- [ ] Performance acceptable
- [ ] Logs reviewed for errors
- [ ] Documentation updated
- [ ] Rollback procedure documented

**Production Deployment:**
- [ ] Staging migration successful
- [ ] Rollback procedure documented
- [ ] Team notified of deployment
- [ ] Maintenance window scheduled (if needed)
- [ ] Monitoring configured
- [ ] Backup taken (if possible)
