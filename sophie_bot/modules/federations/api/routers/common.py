from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import HTTPException, status

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation, FederationBan
from sophie_bot.modules.federations.services.permissions import FederationPermissionService


async def _require_federation_access(federation: Federation, user: ChatModel) -> None:
    if user.tid == CONFIG.owner_id:
        return
    if await FederationPermissionService.is_federation_owner(federation, user.tid):
        return
    if await FederationPermissionService.is_federation_admin(federation, user.tid):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this federation")


async def _require_federation_owner(federation: Federation, user: ChatModel) -> None:
    if user.tid == CONFIG.owner_id:
        return
    if not await FederationPermissionService.is_federation_owner(federation, user.tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only federation owners can perform this action"
        )


async def _require_federation_admin(federation: Federation, user: ChatModel) -> None:
    if user.tid == CONFIG.owner_id:
        return
    if not await FederationPermissionService.is_federation_admin(federation, user.tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only federation admins can perform this action"
        )


def _require_ban_id(federation_ban: FederationBan) -> PydanticObjectId:
    if not federation_ban.id:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ban ID missing")
    return federation_ban.id
