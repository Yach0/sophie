from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from beanie.odm.operators.update.general import Set
from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.locks import LocksModel
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type
from sophie_bot.utils.api.auth import rest_require_admin

from .schemas import LocksPayload, LocksResponse

router = APIRouter()


@router.put("/locked/{chat_iid}", response_model=LocksResponse)
async def set_locked_types(
    chat_iid: PydanticObjectId,
    payload: LocksPayload,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
):
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    valid_locks = [lock_type for lock_type in payload.locked if is_supported_lock_type(lock_type)]

    await LocksModel.find_one(LocksModel.chat.id == chat.iid).upsert(
        Set({LocksModel.locked_types: set(valid_locks)}),
        on_insert=LocksModel(chat=chat.iid, locked_types=set(valid_locks)),
    )
    return LocksResponse(locked=sorted(valid_locks))
