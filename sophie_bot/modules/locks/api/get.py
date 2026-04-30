from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.locks import LocksModel
from sophie_bot.utils.api.auth import rest_require_admin

from .schemas import LocksResponse

router = APIRouter()


@router.get("/locked/{chat_iid}", response_model=LocksResponse)
async def get_locked_types(
    chat_iid: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin())],
):
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    locked_types = await LocksModel.get_locked_types(chat.iid)
    return LocksResponse(locked=sorted(locked_types))
