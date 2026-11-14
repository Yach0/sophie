"""Unset explicit null enabled values from ai_moderator documents.

Affected collections: ai_moderator.
Low risk, small settings collection.
Removes literal null enabled fields in MongoDB so legacy/stable instances expecting
a boolean do not fail with Pydantic ValidationError. Missing fields evaluate to
None under the modern model schema and default to True under the legacy schema.
Backward restores explicit null on documents where enabled is missing.
"""

from __future__ import annotations

from beanie import free_fall_migration
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.services.migrations import MigrationResources


class Forward:
    """Unset explicit null enabled values from ai_moderator documents."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_moderator"]
        await collection.update_many({"enabled": None}, {"$unset": {"enabled": ""}}, session=session)


class Backward:
    """Restore explicit null enabled values for missing enabled fields."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_moderator"]
        await collection.update_many({"enabled": {"$exists": False}}, {"$set": {"enabled": None}}, session=session)
