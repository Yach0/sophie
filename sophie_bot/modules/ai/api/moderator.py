from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException

from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.utils.api.dependencies import RestrictAdminDep

from .schemas import ModeratorSettingsResponse, ModeratorSettingsUpdate

router = APIRouter(prefix="/moderator", tags=["ai_moderator"])


@router.get("/{chat_iid}", response_model=ModeratorSettingsResponse)
async def get_moderator_settings(
    chat_iid: PydanticObjectId,
    user: RestrictAdminDep,
) -> ModeratorSettingsResponse:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    settings = await AIModeratorModel.find_one(AIModeratorModel.chat.id == chat_iid)
    if not settings:
        # Return defaults if not explicitly set
        return ModeratorSettingsResponse(
            enabled=False,
            sexual=DetectionLevel.NORMAL,
            hate_and_discrimination=DetectionLevel.NORMAL,
            violence_and_threats=DetectionLevel.NORMAL,
            dangerous_and_criminal_content=DetectionLevel.NORMAL,
            selfharm=DetectionLevel.NORMAL,
            health=DetectionLevel.NORMAL,
            financial=DetectionLevel.NORMAL,
            law=DetectionLevel.NORMAL,
            pii=DetectionLevel.NORMAL,
        )

    return ModeratorSettingsResponse(**settings.model_dump())


@router.patch("/{chat_iid}", response_model=ModeratorSettingsResponse)
async def update_moderator_settings(
    chat_iid: PydanticObjectId,
    data: ModeratorSettingsUpdate,
    user: RestrictAdminDep,
) -> ModeratorSettingsResponse:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    settings = await AIModeratorModel.find_one(AIModeratorModel.chat.id == chat_iid)
    if not settings:
        settings = AIModeratorModel(chat=chat)

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(settings, key, value)

    await settings.save()
    return ModeratorSettingsResponse(**settings.model_dump())
