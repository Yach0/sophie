from __future__ import annotations

from fastapi import APIRouter

from sophie_bot.db.models.locks import LocksModel
from sophie_bot.utils.api.dependencies import ChatDep, ReadAdminDep

from .schemas import LocksResponse

router = APIRouter()


@router.get("/locked/{chat_iid}", response_model=LocksResponse)
async def get_locked_types(
    chat: ChatDep,
    user: ReadAdminDep,
):
    locked_types = await LocksModel.get_locked_types(chat.iid)
    return LocksResponse(locked=sorted(locked_types))
