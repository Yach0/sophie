from __future__ import annotations

from typing import Annotated

from beanie.odm.operators.find.comparison import In
from fastapi import APIRouter, Depends, HTTPException, status

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.utils.api.auth import get_current_user

from ..schemas import FederationSubscriptionAdd, FederationSubscriptionResponse
from .common import _require_federation_access, _require_federation_owner

router = APIRouter()


@router.get("/{fed_id}/subscriptions", response_model=list[FederationSubscriptionResponse])
async def list_federation_subscriptions(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> list[FederationSubscriptionResponse]:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_access(federation, user)

    subscription_ids = federation.subscribed or []
    if not subscription_ids:
        return []

    subscriptions = await Federation.find(In(Federation.fed_id, subscription_ids)).to_list()
    return [
        FederationSubscriptionResponse(fed_id=sub_federation.fed_id, fed_name=sub_federation.fed_name)
        for sub_federation in subscriptions
    ]


@router.post("/{fed_id}/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe_federation(
    fed_id: str,
    payload: FederationSubscriptionAdd,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    success = await FederationManageService.subscribe_to_federation(federation, payload.target_fed_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Subscription failed")


@router.delete("/{fed_id}/subscriptions/{target_fed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_federation(
    fed_id: str,
    target_fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    success = await FederationManageService.unsubscribe_from_federation(federation, target_fed_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unsubscription failed")
