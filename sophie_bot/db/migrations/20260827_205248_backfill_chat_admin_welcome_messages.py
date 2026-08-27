"""Migration: backfill_chat_admin_welcome_messages

Description:
    Backfills missing `can_send_welcome_messages` permission field on legacy `ChatAdminModel`
    documents in MongoDB.

    Aiogram/Telegram Bot API added `can_send_welcome_messages` as a required boolean field on
    `ChatMemberAdministrator`. Legacy documents saved before this update lack the field, causing
    `ValidationError` when read from MongoDB (SOPHIE-29E, SOPHIE-29G, SOPHIE-29J).

Affected Collections:
    - chat_admin

Impact:
    - Low risk; updates existing administrator member documents without the field.
    - Backward is a no-op: newly saved documents may legitimately have can_send_welcome_messages=False.
"""

from __future__ import annotations

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.chat_admin import ChatAdminModel


async def backfill_chat_admin_welcome_messages(chat_admin: Any, session: Any = None) -> int:
    """Backfill missing can_send_welcome_messages field on administrator members."""
    result = await chat_admin.update_many(
        {"member.status": "administrator", "member.can_send_welcome_messages": {"$exists": False}},
        {"$set": {"member.can_send_welcome_messages": False}},
        session=session,
    )
    return result.modified_count


class Forward:
    """Backfill missing can_send_welcome_messages field on administrator members."""

    @free_fall_migration(document_models=[ChatAdminModel])
    async def backfill(self, session) -> None:
        await backfill_chat_admin_welcome_messages(ChatAdminModel.get_pymongo_collection(), session=session)


class Backward:
    """No rollback: newly saved documents may legitimately have can_send_welcome_messages=False."""

    @free_fall_migration(document_models=[ChatAdminModel])
    async def noop(self, session) -> None:
        del session
