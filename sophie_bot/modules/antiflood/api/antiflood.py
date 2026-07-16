from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from sophie_bot.constants import ANTIFOOD_MAX_ACTIONS
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FilterActionType
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.utils.api.dependencies import RestrictAdminDep

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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid action name: {v}. Valid actions: {', '.join(ALL_MODERN_ACTIONS.keys())}",
            )
        action = ALL_MODERN_ACTIONS[v]
        if not action.as_flood:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Action '{v}' cannot be used as an antiflood action",
            )
        return v

    @model_validator(mode="after")
    def validate_action_data(self) -> ActionRequest:
        """Validate the data against the action's own model and canonicalize it for storage."""
        action = ALL_MODERN_ACTIONS[self.name]
        data_object = getattr(action, "data_object", None)
        if data_object is None:
            return self

        if not self.data and action.default_data is not None:
            self.data = action.default_data.model_dump(mode="json")
            return self

        try:
            validated_data = data_object(**self.data)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid action data for '{self.name}': {exc}",
            ) from exc

        self.data = validated_data.model_dump(mode="json")
        return self


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
    settings.actions = [FilterActionType(name=action.name, data=action.data) for action in request.actions]

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
