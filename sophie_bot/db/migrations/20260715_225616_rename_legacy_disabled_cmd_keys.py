"""Migration: rename_legacy_disabled_cmd_keys

Description:
    `/disable` used to store a handler's first command instead of its canonical disable-able name,
    which is what the enforcing middleware reads. For the two handlers whose canonical name differs
    from their first command the stored key was never enforced. Rewrites those keys to the canonical
    name, so the commands a chat was told are disabled actually become disabled.

Affected Collections:
    - disabled

Impact:
    - Low risk: rewrites two known strings inside `cmds`
    - `/aitranslate` and `/enableantiflood` become enforced in chats that had disabled them
    - Chats that never disabled those commands are untouched
"""

from __future__ import annotations

from typing import Any

from beanie import free_fall_migration

from sophie_bot.db.models.disabling import DisablingModel

LEGACY_KEYS_TO_CANONICAL = {
    "aitranslate": "translate",
    "enableantiflood": "antiflood",
}
CANONICAL_TO_LEGACY_KEYS = {canonical: legacy for legacy, canonical in LEGACY_KEYS_TO_CANONICAL.items()}


async def rename_disabled_cmd_keys(session: Any, renames: dict[str, str]) -> int:
    collection = DisablingModel.get_pymongo_collection()
    renamed_count = 0

    async for doc in collection.find({"cmds": {"$in": list(renames)}}, session=session):
        renamed = list(dict.fromkeys(renames.get(cmd, cmd) for cmd in doc["cmds"]))

        await collection.update_one({"_id": doc["_id"]}, {"$set": {"cmds": renamed}}, session=session)
        renamed_count += 1

    return renamed_count


class Forward:
    """Rewrite legacy command keys to their canonical disable-able names."""

    @free_fall_migration(document_models=[DisablingModel])
    async def migrate(self, session):
        count = await rename_disabled_cmd_keys(session, LEGACY_KEYS_TO_CANONICAL)
        print(f"Renamed legacy disabled command keys in {count} chats")


class Backward:
    """Restore the legacy command keys."""

    @free_fall_migration(document_models=[DisablingModel])
    async def rollback(self, session):
        await rename_disabled_cmd_keys(session, CANONICAL_TO_LEGACY_KEYS)
