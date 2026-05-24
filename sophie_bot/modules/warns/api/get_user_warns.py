from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel
from sophie_bot.utils.api.auth import rest_require_admin
from sophie_bot.utils.api.dependencies import get_chat_or_404

from .schemas import WarnResponse

router = APIRouter(prefix="/warns", tags=["warns"])


@router.get("/{chat_iid}/{user_tid}", response_model=List[WarnResponse])
async def get_user_warns(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    user_tid: int,
    current_user: Annotated[ChatModel, Depends(rest_require_admin("can_restrict_members"))],
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
