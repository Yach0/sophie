from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from sophie_bot.constants import ANTIFOOD_MAX_ACTIONS
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.services.rest import ServicesDep
from sophie_bot.shared.action_registry import normalize_action_data
from sophie_bot.shared.actions import (
    ActionDefinition,
    ActionValidationError,
    StoredAction,
)
from sophie_bot.utils.api.dependencies import RestrictAdminDep

router = APIRouter(prefix="/antiflood", tags=["antiflood"])


class ActionRequest(BaseModel):
    name: str = Field(..., description="Action name (e.g., 'mute_user', 'kick_user', 'ban_user')")
    data: dict[str, Any] = Field(default_factory=dict, description="Action-specific data")


class AntifloodSettingsRequest(BaseModel):
    enabled: bool = True
    message_count: int = Field(default=5, ge=1, le=100)
    actions: list[ActionRequest] = Field(
        default_factory=list,
        max_length=ANTIFOOD_MAX_ACTIONS,
        description=f"List of actions (max {ANTIFOOD_MAX_ACTIONS})",
    )


def _validate_action_request(
    request: ActionRequest,
    actions: dict[str, ActionDefinition[Any]],
) -> StoredAction:
    try:
        data = normalize_action_data(
            actions,
            request.name,
            request.data,
            capability="flood",
        )
    except ActionValidationError as error:
        if error.reason == "unknown":
            detail = f"Invalid action name: {request.name}. Valid actions: {', '.join(actions)}"
        elif error.reason == "capability":
            detail = f"Action '{request.name}' cannot be used as an antiflood action"
        else:
            detail = error.detail
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=detail,
        ) from error
    return StoredAction(name=request.name, data=data)


class ActionResponse(BaseModel):
    name: str
    data: dict


class AntifloodSettingsResponse(BaseModel):
    chat_iid: PydanticObjectId
    chat_tid: int
    enabled: bool
    message_count: int
    actions: list[ActionResponse]


@router.get("/{chat_iid}", response_model=AntifloodSettingsResponse)
async def get_antiflood_settings(
    chat_iid: PydanticObjectId,
    user: RestrictAdminDep,
) -> AntifloodSettingsResponse:
    """Get antiflood settings for a chat."""
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

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
    user: RestrictAdminDep,
    services: ServicesDep,
) -> AntifloodSettingsResponse:
    """Update antiflood settings for a chat."""
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

    settings = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)

    if not settings:
        settings = AntifloodModel(chat=chat)

    settings.enabled = request.enabled
    settings.message_count = request.message_count
    settings.actions = [_validate_action_request(action, services.modules.actions) for action in request.actions]

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
    user: RestrictAdminDep,
) -> None:
    """Disable antiflood for a chat (deletes settings)."""
    settings = await AntifloodModel.find_one(AntifloodModel.chat.id == chat_iid)
    if settings:
        await settings.delete()
