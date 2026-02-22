from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import In
from fastapi import APIRouter, Depends, HTTPException, status

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.federations.services import FederationChatService, FederationManageService
from sophie_bot.utils.api.auth import get_current_user, rest_require_admin

from ..schemas import FederationChatAdd, FederationChatResponse
from .common import _require_federation_access

router = APIRouter()


@router.get("/{fed_id}/chats", response_model=list[FederationChatResponse])
async def list_federation_chats(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> list[FederationChatResponse]:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_access(federation, user)

    if not federation.chats:
        return []

    chat_iids = [chat_link.to_ref().id for chat_link in federation.chats]
    chats = await ChatModel.find(In(ChatModel.iid, chat_iids)).to_list()
    return [
        FederationChatResponse(
            chat_iid=chat_model.iid,
            title=chat_model.first_name_or_title,
            username=chat_model.username,
        )
        for chat_model in chats
    ]


@router.post("/{fed_id}/chats", status_code=status.HTTP_204_NO_CONTENT)
async def add_chat_to_federation(
    fed_id: str,
    payload: FederationChatAdd,
    user: Annotated[ChatModel, Depends(rest_require_admin(require_owner=True))],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")

    chat = await ChatModel.get_by_iid(payload.chat_iid)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    existing_federation = await FederationManageService.get_federation_for_chat(chat.iid)
    if existing_federation:
        if existing_federation.fed_id == federation.fed_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chat already in this federation")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chat already in another federation")

    joined = await FederationChatService.add_chat_to_federation(federation, chat.iid)
    if not joined:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chat already in this federation")


@router.delete("/{fed_id}/chats/{chat_iid}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_chat_from_federation(
    fed_id: str,
    chat_iid: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin(require_owner=True))],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")

    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    existing_federation = await FederationManageService.get_federation_for_chat(chat.iid)
    if not existing_federation or existing_federation.fed_id != federation.fed_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat is not in this federation")

    removed = await FederationChatService.remove_chat_from_federation(federation, chat.iid)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat is not in this federation")
