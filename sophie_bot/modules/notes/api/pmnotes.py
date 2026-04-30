from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.privatenotes import PrivateNotesModel
from sophie_bot.utils.api.auth import rest_require_admin

from .schemas import PMNotesStateResponse, PMNotesStateUpdate

router = APIRouter(prefix="/pmnotes")


@router.get("/{chat_iid}", response_model=PMNotesStateResponse)
async def get_pmnotes_state(
    chat_iid: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin())],
) -> PMNotesStateResponse:
    chat = await ChatModel.get(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    enabled = await PrivateNotesModel.get_state(chat_iid)
    return PMNotesStateResponse(enabled=enabled)


@router.patch("/{chat_iid}", response_model=PMNotesStateResponse)
async def update_pmnotes_state(
    chat_iid: PydanticObjectId,
    update: PMNotesStateUpdate,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
) -> PMNotesStateResponse:
    _ = user
    chat = await ChatModel.get(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    await PrivateNotesModel.set_state(chat_iid, update.enabled)

    return PMNotesStateResponse(enabled=update.enabled)
