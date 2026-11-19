from fastapi import APIRouter, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel
from sophie_bot.modules.warns.utils import expire_warns
from sophie_bot.services.rest import ServicesDep
from sophie_bot.utils.api.dependencies import ChatDep, RestrictAdminDep

from .schemas import WarnResponse

router = APIRouter(prefix="/warns", tags=["warns"])


@router.get("/{chat_iid}/{user_tid}", response_model=list[WarnResponse])
async def get_user_warns(
    chat: ChatDep,
    user_tid: int,
    _current_user: RestrictAdminDep,
    services: ServicesDep,
) -> list[WarnResponse]:
    user = await ChatModel.get_by_tid(user_tid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await expire_warns(chat, redis=services.redis)
    warns = await WarnModel.get_user_warns(chat.iid, user.iid)

    return [
        WarnResponse(
            id=str(warn.id),
            user_id=user_tid,
            admin_id=warn.admin.tid if warn.admin else None,
            reason=warn.reason,
            date=warn.date.isoformat() if warn.date else None,
        )
        for warn in warns
    ]
