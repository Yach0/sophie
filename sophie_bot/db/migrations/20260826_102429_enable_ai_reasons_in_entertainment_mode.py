"""Migration: enable_ai_reasons_in_entertainment_mode

Description:
    Gives entertainment-mode chats the same catalog-backed AI restriction-reason availability as
    the other enabled group modes. For each model with a support or moderation reason role, an
    entertainment role is copied with the same priority and runtime settings.

Affected Collections:
    - ai_catalog_model

Impact:
    - Low risk: only adds a missing role and is idempotent.
    - Small collection: the operator-managed AI model catalog.

Rollback:
    Removes entertainment-mode moderation-reason roles.
"""

from __future__ import annotations

from beanie import free_fall_migration
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.services.db import get_collection

_PURPOSE = "moderation_reason"
_TARGET_MODE = "entertainment"
_SOURCE_MODES = ("support", "moderation")


def _add_entertainment_reason_role(roles: list[dict[str, object]]) -> list[dict[str, object]]:
    if any(role.get("mode") == _TARGET_MODE and role.get("purpose") == _PURPOSE for role in roles):
        return roles

    source_role = next(
        (
            role
            for source_mode in _SOURCE_MODES
            for role in roles
            if role.get("mode") == source_mode and role.get("purpose") == _PURPOSE
        ),
        None,
    )
    if source_role is None:
        return roles

    return [*roles, {**source_role, "mode": _TARGET_MODE}]


def _remove_entertainment_reason_role(roles: list[dict[str, object]]) -> list[dict[str, object]]:
    return [role for role in roles if not (role.get("mode") == _TARGET_MODE and role.get("purpose") == _PURPOSE)]


class Forward:
    """Copy each model's moderation-reason role into entertainment mode."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None) -> None:
        collection = get_collection("ai_catalog_model")
        async for model in collection.find({"roles.purpose": _PURPOSE}, session=session):
            roles = model.get("roles", [])
            updated_roles = _add_entertainment_reason_role(roles)
            if updated_roles != roles:
                await collection.update_one({"_id": model["_id"]}, {"$set": {"roles": updated_roles}}, session=session)


class Backward:
    """Remove the role combination introduced by the forward migration."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None) -> None:
        collection = get_collection("ai_catalog_model")
        async for model in collection.find(
            {"roles": {"$elemMatch": {"mode": _TARGET_MODE, "purpose": _PURPOSE}}}, session=session
        ):
            await collection.update_one(
                {"_id": model["_id"]},
                {"$set": {"roles": _remove_entertainment_reason_role(model.get("roles", []))}},
                session=session,
            )
