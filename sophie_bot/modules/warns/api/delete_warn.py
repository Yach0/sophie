from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel
from sophie_bot.utils.api.auth import rest_require_admin
from sophie_bot.utils.api.dependencies import get_chat_or_404

router = APIRouter(prefix="/warns", tags=["warns"])


@router.delete("/{chat_iid}/{warn_iid}")
async def delete_warn(
    chat: Annotated[ChatModel, Depends(get_chat_or_404)],
    warn_iid: str,
    current_user: Annotated[ChatModel, Depends(rest_require_admin("can_restrict_members"))],
) -> dict:
    try:
        warn_obj_id = PydanticObjectId(warn_iid)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid warn_iid")

    warn = await WarnModel.find_one(WarnModel.id == warn_obj_id, WarnModel.chat.id == chat.iid)
    if not warn:
        raise HTTPException(status_code=404, detail="Warning not found")

    await warn.delete()
    return {"status": "ok"}
