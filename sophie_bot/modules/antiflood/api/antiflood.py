from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from sophie_bot.constants import ANTIFOOD_MAX_ACTIONS
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.db.models.filters import FilterActionType
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.utils.api.auth import get_current_user

router = APIRouter(prefix="/antiflood", tags=["antiflood"])


class ActionRequest(BaseModel):
    name: str = Field(..., description="Action name (e.g., 'mute_user', 'kick_user', 'ban_user')")
    data: dict = Field(default_factory=dict, description="Action-specific data")

    @field_validator("name")
    @classmethod
    def validate_action_name(cls, v: str) -> str:
        """Validate that action name exists and supports flood actions."""
        if v not in ALL_MODERN_ACTIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid action name: {v}. Valid actions: {', '.join(ALL_MODERN_ACTIONS.keys())}",
            )
        action = ALL_MODERN_ACTIONS[v]
        if not action.as_flood:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Action '{v}' cannot be used as an antiflood action",
            )
        return v

    @field_validator("data")
    @classmethod
    def validate_action_data(cls, v: dict) -> dict:
        """Validate action data based on the action type."""
        action_name = v.get("name")
        if not action_name:
            return v

        action = ALL_MODERN_ACTIONS.get(action_name)
        if not action or not action.data_object:
            return v

        # If action has a data model, validate it
        try:
            action.data_object(**v)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid action data for '{action_name}': {str(e)}",
            )
        return v


class AntifloodSettingsRequest(BaseModel):
    enabled: bool = True
    message_count: int = Field(default=5, ge=1, le=100)
    actions: list[ActionRequest] = Field(
        default_factory=list,
        max_length=ANTIFOOD_MAX_ACTIONS,
        description=f"List of actions (max {ANTIFOOD_MAX_ACTIONS})",
    )


class ActionResponse(BaseModel):
    name: str
    data: dict


class AntifloodSettingsResponse(BaseModel):
    chat_iid: PydanticObjectId
    chat_tid: int
    enabled: bool
    message_count: int
    actions: list[ActionResponse]


async def verify_chat_admin(
    user: ChatModel,
    chat_iid: PydanticObjectId,
) -> ChatModel:
    """Verify that the user is an admin in the specified chat."""
    admin = await ChatAdminModel.find_one(
        ChatAdminModel.chat.id == chat_iid,
        ChatAdminModel.user.id == user.iid,
    )
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not an admin in this chat",
        )
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )
    return chat


@router.get("/{chat_iid}", response_model=AntifloodSettingsResponse)
async def get_antiflood_settings(
    chat_iid: PydanticObjectId,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> AntifloodSettingsResponse:
    """Get antiflood settings for a chat."""
    chat = await verify_chat_admin(user, chat_iid)

    settings = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)

    if not settings:
        return AntifloodSettingsResponse(
            chat_iid=chat_iid,
            chat_tid=chat.tid,
            enabled=False,
            message_count=5,
            actions=[],
        )

    return AntifloodSettingsResponse(
        chat_iid=chat_iid,
        chat_tid=chat.tid,
        enabled=settings.enabled or False,
        message_count=settings.message_count,
        actions=[ActionResponse(name=action.name, data=action.data or {}) for action in settings.actions],
    )


@router.put("/{chat_iid}", response_model=AntifloodSettingsResponse)
async def update_antiflood_settings(
    chat_iid: PydanticObjectId,
    request: AntifloodSettingsRequest,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> AntifloodSettingsResponse:
    """Update antiflood settings for a chat."""
    chat = await verify_chat_admin(user, chat_iid)

    settings = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)

    if not settings:
        settings = AntifloodModel(chat=chat)

    settings.enabled = request.enabled
    settings.message_count = request.message_count
    settings.actions = [FilterActionType(name=action.name, data=action.data) for action in request.actions]
    settings.action = None  # Clear legacy action

    await settings.save()

    return AntifloodSettingsResponse(
        chat_iid=chat_iid,
        chat_tid=chat.tid,
        enabled=settings.enabled or False,
        message_count=settings.message_count,
        actions=[ActionResponse(name=action.name, data=action.data or {}) for action in settings.actions],
    )


@router.delete("/{chat_iid}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_antiflood(
    chat_iid: PydanticObjectId,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    """Disable antiflood for a chat (deletes settings)."""
    await verify_chat_admin(user, chat_iid)

    settings = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)
    if settings:
        await settings.delete()
