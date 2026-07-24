"""Migration: migrate_ai_settings_to_mode

Description:
    Replaces the per-chat AI on/off switch, provider choice and moderator toggle with a single AI
    mode. Chats that never enabled AI become `disabled`; chats with the AI moderator on become
    `moderation`; every other AI-enabled chat becomes `support`, which is the closest match to the
    previous default behaviour (chatbot on, replies triggered, moderator on where configured).

Affected Collections:
    - ai_mode (created)
    - ai_enabled (dropped)
    - ai_provider (dropped)

Impact:
    - Medium risk: this is the only source of a chat's AI behaviour after the change. A chat missing
      from ai_mode has every AI feature off.
    - The provider choice is not carried over: models are now selected by mode.

Rollback:
    Recreates ai_enabled from every non-disabled mode and drops ai_mode. The provider choice cannot
    be restored, so rolled-back chats fall back to the "auto" provider default.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection


class Forward:
    """Derive one AI mode per chat from the old enabled/moderator documents."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        enabled_chats = await get_collection("ai_enabled").distinct("chat", session=session)
        moderated_chats = set(await get_collection("ai_moderator").distinct("chat", {"enabled": True}, session=session))

        documents = [
            {"chat": chat, "mode": "moderation" if chat in moderated_chats else "support"} for chat in enabled_chats
        ]
        if documents:
            await get_collection("ai_mode").insert_many(documents, session=session)

        await get_collection("ai_enabled").drop(session=session)
        await get_collection("ai_provider").drop(session=session)

        print(f"Migrated {len(documents)} chats to AI modes")


class Backward:
    """Recreate ai_enabled from every chat whose mode is not disabled."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        enabled_chats = await get_collection("ai_mode").distinct("chat", {"mode": {"$ne": "disabled"}}, session=session)
        if enabled_chats:
            await get_collection("ai_enabled").insert_many([{"chat": chat} for chat in enabled_chats], session=session)

        await get_collection("ai_mode").drop(session=session)
