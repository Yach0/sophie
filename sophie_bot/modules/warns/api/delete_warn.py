from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException

from sophie_bot.db.models.warns import WarnModel
from sophie_bot.utils.api.dependencies import ChatDep, RestrictAdminDep

router = APIRouter(prefix="/warns", tags=["warns"])


@router.delete("/{chat_iid}/{warn_iid}")
async def delete_warn(
    chat: ChatDep,
    warn_iid: str,
    current_user: RestrictAdminDep,
) -> dict:
    try:
        warn_obj_id = PydanticObjectId(warn_iid)
    except Exception:  # noqa: BLE001  # any parse failure is an invalid id -> 400
        raise HTTPException(status_code=400, detail="Invalid warn_iid")

    warn = await WarnModel.find_one(WarnModel.id == warn_obj_id, WarnModel.chat.id == chat.iid)
    if not warn:
        raise HTTPException(status_code=404, detail="Warning not found")

    await warn.delete()
    return {"status": "ok"}
