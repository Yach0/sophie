from typing import List

from fastapi import APIRouter, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel
from sophie_bot.utils.api.dependencies import ChatDep, RestrictAdminDep

from .schemas import WarnResponse

router = APIRouter(prefix="/warns", tags=["warns"])


@router.get("/{chat_iid}/{user_tid}", response_model=List[WarnResponse])
async def get_user_warns(
    chat: ChatDep,
    user_tid: int,
    current_user: RestrictAdminDep,
) -> List[WarnResponse]:
    user = await ChatModel.get_by_tid(user_tid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    warns = await WarnModel.get_user_warns(chat.iid, user.iid)

    return [
        WarnResponse(
            id=str(warn.id),
            user_id=user_tid,
            admin_id=warn.admin.tid if warn.admin else None,
            reason=warn.reason,
            date=warn.date.isoformat(),
        )
        for warn in warns
    ]
