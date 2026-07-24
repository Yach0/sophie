"""Migration: expand_wildcard_roles

Description:
    Resolution no longer has an any-mode wildcard or a support-tier fallback — every (mode, purpose)
    a chat uses must carry its own catalog role or Sophie crashes. This rewrites the old data to fit:

      * every ``mode=None`` role is expanded into an explicit per-mode role for each mode that can
        use that purpose (see the capability matrix below);
      * the two private-chat modes (``sophie_pm``, ``sophie_help``), which previously borrowed the
        support tier via the fallback, are given their own roles by mirroring the support ones.

Affected Collections:
    - ai_catalog_model

Impact:
    - Medium risk: reshapes the ``roles`` array of every catalog model. Idempotent — re-running
      produces the same set of per-mode roles.

Rollback:
    Collapses the per-mode roles of the previously-global purposes back into one ``mode=None`` role
    and drops the private-chat-mode roles. Per-mode service tiers on those purposes are not restored.
"""

from beanie import free_fall_migration

from sophie_bot.services.db import get_collection

# Frozen copy of the capability matrix (sophie_bot.modules.ai.utils.ai_catalog.MODE_PURPOSES) as of
# this migration. Kept literal so a later change to the matrix cannot retro-alter this migration.
_MODE_PURPOSES: dict[str, set[str]] = {
    "entertainment": {"chatbot", "translation", "filters", "summary", "research"},
    "moderation": {"chatbot", "translation", "filters", "moderation_reason", "research"},
    "support": {"chatbot", "translation", "filters", "summary", "moderation_reason", "research"},
    "sophie_pm": {"chatbot", "translation", "research"},
    "sophie_help": {"chatbot", "sophie_inspect"},
}

# Purposes that used to be configured once with a ``mode=None`` role.
_GLOBAL_PURPOSES = {"summary", "moderation_reason", "sophie_inspect", "research"}

_PRIVATE_MODES = ("sophie_pm", "sophie_help")


def _role_settings(role: dict) -> dict:
    """The service-tier / reasoning-effort a role carries, to preserve when copying it to a mode."""
    return {key: role[key] for key in ("service_tier", "reasoning_effort") if key in role}


def _expand_roles(roles: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}

    def put(mode: str, purpose: str, settings: dict) -> None:
        by_key.setdefault((mode, purpose), {"mode": mode, "purpose": purpose, **settings})

    for role in roles:
        purpose = role["purpose"]
        settings = _role_settings(role)
        if role.get("mode") is None:
            for mode, purposes in _MODE_PURPOSES.items():
                if purpose in purposes:
                    put(mode, purpose, settings)
        elif role["mode"] in _MODE_PURPOSES:
            put(role["mode"], purpose, settings)

    # The private-chat modes had no roles of their own; mirror the support ones they can use.
    for private_mode in _PRIVATE_MODES:
        for purpose in _MODE_PURPOSES[private_mode]:
            support_role = by_key.get(("support", purpose))
            if support_role is not None:
                put(private_mode, purpose, _role_settings(support_role))

    return list(by_key.values())


class Forward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        collection = get_collection("ai_catalog_model")
        async for model in collection.find({}, session=session):
            await collection.update_one(
                {"_id": model["_id"]},
                {"$set": {"roles": _expand_roles(model.get("roles", []))}},
                session=session,
            )


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session) -> None:
        collection = get_collection("ai_catalog_model")
        async for model in collection.find({}, session=session):
            collapsed: list[dict] = []
            globals_seen: set[str] = set()
            for role in model.get("roles", []):
                if role.get("mode") in _PRIVATE_MODES:
                    continue
                purpose = role["purpose"]
                if purpose in _GLOBAL_PURPOSES:
                    if purpose not in globals_seen:
                        globals_seen.add(purpose)
                        collapsed.append({"mode": None, "purpose": purpose})
                    continue
                collapsed.append(role)
            await collection.update_one({"_id": model["_id"]}, {"$set": {"roles": collapsed}}, session=session)
