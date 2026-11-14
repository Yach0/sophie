from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode
from sophie_bot.modules.ai.utils.moderation.settings import get_enabled, get_levels
from sophie_bot.utils.api.dependencies import RestrictAdminDep

from .schemas import ModeratorSettingsResponse, ModeratorSettingsUpdate

router = APIRouter(prefix="/moderator", tags=["ai_moderator"])


@router.get("/{chat_iid}", response_model=ModeratorSettingsResponse)
async def get_moderator_settings(
    chat_iid: PydanticObjectId,
    _user: RestrictAdminDep,
) -> ModeratorSettingsResponse:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    settings = await AIModeratorModel.find_one(AIModeratorModel.chat.id == chat_iid)
    return await _settings_response(chat, settings)


@router.patch("/{chat_iid}", response_model=ModeratorSettingsResponse)
async def update_moderator_settings(
    chat_iid: PydanticObjectId,
    data: ModeratorSettingsUpdate,
    _user: RestrictAdminDep,
) -> ModeratorSettingsResponse:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    settings = await AIModeratorModel.update_fields(chat, data.model_dump(exclude_unset=True))
    return await _settings_response(chat, settings)


async def _settings_response(chat: ChatModel, settings: AIModeratorModel | None) -> ModeratorSettingsResponse:
    mode = await get_chat_mode(chat.iid, AIMode.disabled)
    values = {category.value: level for category, level in get_levels(settings).items()}
    return ModeratorSettingsResponse(enabled=get_enabled(settings, mode), **values)
