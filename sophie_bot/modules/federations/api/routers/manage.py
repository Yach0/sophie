from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.services import FederationManageService
from sophie_bot.utils.api.auth import get_current_user

from ..schemas import (
    FederationCreate,
    FederationDetailResponse,
    FederationLogChannelUpdate,
    FederationSummaryResponse,
    FederationUpdate,
)
from .common import _require_federation_access, _require_federation_owner

router = APIRouter()


def _federation_summary(federation: Federation) -> FederationSummaryResponse:
    return FederationSummaryResponse(
        fed_id=federation.fed_id,
        fed_name=federation.fed_name,
        creator_iid=federation.creator.id,
        log_chat_iid=federation.log_chat.iid if federation.log_chat else None,
    )


def _federation_detail(federation: Federation) -> FederationDetailResponse:
    chat_iids = [c.iid for c in federation.chats] if federation.chats else []
    return FederationDetailResponse(
        fed_id=federation.fed_id,
        fed_name=federation.fed_name,
        creator_iid=federation.creator.id,
        log_chat_iid=federation.log_chat.iid if federation.log_chat else None,
        chat_iids=chat_iids,
        subscribed_fed_ids=federation.subscribed or [],
    )


@router.get("/", response_model=list[FederationSummaryResponse])
async def list_federations(
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> list[FederationSummaryResponse]:
    owned_federations = await Federation.find(Federation.creator.id == user.iid).to_list()
    admin_federations = await Federation.find(Federation.admins == user.iid).to_list()

    unique_federations: dict[str, Federation] = {federation.fed_id: federation for federation in owned_federations}
    for federation in admin_federations:
        unique_federations.setdefault(federation.fed_id, federation)

    federations_list = list(unique_federations.values())
    return [_federation_summary(federation) for federation in federations_list]


@router.post("/", response_model=FederationSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_federation(
    payload: FederationCreate,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> FederationSummaryResponse:
    federation = await FederationManageService.create_federation(payload.name, user.iid)
    if not federation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to create federation")
    return _federation_summary(federation)


@router.get("/{fed_id}", response_model=FederationDetailResponse)
async def get_federation(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> FederationDetailResponse:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_access(federation, user)
    return _federation_detail(federation)


@router.patch("/{fed_id}", response_model=FederationSummaryResponse)
async def update_federation(
    fed_id: str,
    payload: FederationUpdate,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> FederationSummaryResponse:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    updates: dict[str, str] = {}
    if payload.name:
        updates["fed_name"] = payload.name

    if updates:
        await FederationManageService.update_federation(federation, updates)

    return _federation_summary(federation)


@router.delete("/{fed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_federation(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)
    await FederationManageService.delete_federation(federation)


@router.post("/{fed_id}/log_channel", status_code=status.HTTP_204_NO_CONTENT)
async def set_federation_log_channel(
    fed_id: str,
    payload: FederationLogChannelUpdate,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    if federation.log_chat:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Log channel already set")

    chat = await ChatModel.get_by_iid(payload.chat_iid)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    await FederationManageService.set_federation_log_channel(federation, chat.iid)


@router.delete("/{fed_id}/log_channel", status_code=status.HTTP_204_NO_CONTENT)
async def unset_federation_log_channel(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    if not federation.log_chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log channel not set")

    await FederationManageService.remove_federation_log_channel(federation)
