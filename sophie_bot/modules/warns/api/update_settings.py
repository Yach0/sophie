from fastapi import APIRouter

from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.utils.api.dependencies import ChatDep, RestrictAdminDep

from .schemas import WarnSettingsResponse, WarnSettingsUpdate

router = APIRouter(prefix="/warns", tags=["warns"])


@router.patch("/settings/{chat_iid}", response_model=WarnSettingsResponse)
async def update_warn_settings(
    chat: ChatDep,
    update: WarnSettingsUpdate,
    current_user: RestrictAdminDep,
) -> WarnSettingsResponse:
    settings = await WarnSettingsModel.get_or_create(chat.iid)

    if update.max_warns is not None:
        settings.max_warns = update.max_warns

    await settings.save()

    return WarnSettingsResponse(
        max_warns=settings.max_warns,
        actions=[action.model_dump() for action in settings.on_max_warn_actions],
        on_each_warn_actions=[action.model_dump() for action in settings.on_each_warn_actions],
        on_max_warn_actions=[action.model_dump() for action in settings.on_max_warn_actions],
    )
