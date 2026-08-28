"""Migration: raw_backfill_chat_admin_welcome_messages

Description:
    Raw-update legacy chat administrator documents whose member subdocument is
    missing Telegram's can_send_welcome_messages field. The migration deliberately
    does not register ChatAdminModel, because loading those documents can fail
    validation before the repair is applied.

Affected Collections:
    - chat_admin

Impact:
    - Low risk; sets the missing permission to False and is idempotent.
    - Backward is a no-op because False is valid current data.
"""

from __future__ import annotations

from beanie import free_fall_migration
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.services.db import get_collection


class Forward:
    """Repair legacy administrator documents using a raw MongoDB update."""

    @free_fall_migration(document_models=[])
    async def backfill(self, session: AsyncClientSession | None) -> None:
        collection = get_collection("chat_admin")
        await collection.update_many(
            {
                "member.status": "administrator",
                "member.can_send_welcome_messages": {"$exists": False},
            },
            {"$set": {"member.can_send_welcome_messages": False}},
            session=session,
        )


class Backward:
    """Do nothing because False is also valid for newly saved documents."""

    @free_fall_migration(document_models=[])
    async def noop(self, session: AsyncClientSession | None) -> None:
        del session
