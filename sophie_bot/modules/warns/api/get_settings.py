from fastapi import APIRouter

from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.utils.api.dependencies import ChatDep, ReadAdminDep

from .schemas import WarnSettingsResponse

router = APIRouter(prefix="/warns", tags=["warns"])


@router.get("/settings/{chat_iid}", response_model=WarnSettingsResponse)
async def get_warn_settings(
    chat: ChatDep,
    current_user: ReadAdminDep,
) -> WarnSettingsResponse:
    settings = await WarnSettingsModel.get_or_create(chat.iid)
    return WarnSettingsResponse(
        max_warns=settings.max_warns,
        actions=[action.model_dump() for action in settings.on_max_warn_actions],
        on_each_warn_actions=[action.model_dump() for action in settings.on_each_warn_actions],
        on_max_warn_actions=[action.model_dump() for action in settings.on_max_warn_actions],
    )
