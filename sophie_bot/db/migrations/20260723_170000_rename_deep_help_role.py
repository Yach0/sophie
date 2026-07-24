"""Migration: rename_deep_help_role

Description:
    Renames any leftover ``deep_help`` catalog role to ``sophie_inspect``. The tool was renamed after
    the model-seeding migration first shipped, so a database that ran that migration before the
    rename holds a role the current enum no longer accepts — which makes every catalog read fail to
    parse the row. This converges those databases regardless of which version of the earlier
    migration they ran.

Affected Collections:
    - ai_catalog_model

Impact:
    - Low risk: a targeted rename inside the embedded roles array. No-op where the role is already
      ``sophie_inspect``.

Rollback:
    No-op: ``sophie_inspect`` is the permanent name, and renaming it back would reintroduce a role
    the current enum rejects.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection

_OLD = "deep_help"
_NEW = "sophie_inspect"


class Forward:
    """Point every deep_help role at the renamed sophie_inspect purpose."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        collection = get_collection("ai_catalog_model")
        renamed = 0
        async for document in collection.find({"roles.purpose": _OLD}, session=session):
            roles = document.get("roles", [])
            for role in roles:
                if role.get("purpose") == _OLD:
                    role["purpose"] = _NEW
            await collection.update_one({"_id": document["_id"]}, {"$set": {"roles": roles}}, session=session)
            renamed += 1
        print(f"Renamed deep_help -> sophie_inspect on {renamed} catalog models")


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        del session
