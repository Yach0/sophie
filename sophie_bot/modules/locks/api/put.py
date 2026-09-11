from __future__ import annotations

from typing import Annotated

from beanie.odm.operators.update.general import Set
from fastapi import APIRouter, Depends

from sophie_bot.db.models.locks import LocksModel
from sophie_bot.modules.locks.utils.cache import invalidate_locks_cache
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type
from sophie_bot.services.application import ApplicationServices
from sophie_bot.services.rest import get_services
from sophie_bot.utils.api.dependencies import ChangeInfoAdminDep, ChatDep

from .schemas import LocksPayload, LocksResponse

router = APIRouter()


@router.put("/locked/{chat_iid}", response_model=LocksResponse)
async def set_locked_types(
    chat: ChatDep,
    payload: LocksPayload,
    user: ChangeInfoAdminDep,
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> LocksResponse:
    valid_locks = [lock_type for lock_type in payload.locked if is_supported_lock_type(lock_type)]

    await LocksModel.find_one(LocksModel.chat.id == chat.iid).upsert(
        Set({"locked_types": set(valid_locks)}),
        on_insert=LocksModel(chat=chat.iid, locked_types=set(valid_locks)),
    )
    await invalidate_locks_cache(chat.tid, redis=services.redis)

    return LocksResponse(locked=sorted(valid_locks))
