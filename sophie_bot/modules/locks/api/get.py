from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.locks import LocksModel
from sophie_bot.utils.api.auth import rest_require_admin
from sophie_bot.utils.api.dependencies import get_chat_or_404

from .schemas import LocksResponse

router = APIRouter()


@router.get("/locked/{chat_iid}", response_model=LocksResponse)
async def get_locked_types(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    user: Annotated[ChatModel, Depends(rest_require_admin())],
):
    locked_types = await LocksModel.get_locked_types(chat.iid)
    return LocksResponse(locked=sorted(locked_types))
