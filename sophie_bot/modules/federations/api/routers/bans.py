from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.federations.services import FederationBanService, FederationManageService
from sophie_bot.utils.api.auth import get_current_user

from ..schemas import FederationBanCreate, FederationBanResponse
from .common import _require_ban_id, _require_federation_admin

router = APIRouter()


@router.get("/{fed_id}/bans", response_model=list[FederationBanResponse])
async def list_federation_bans(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> list[FederationBanResponse]:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_admin(federation, user)

    bans = await FederationBanService.get_federation_bans(fed_id)
    result = []
    for ban in bans:
        by_user = await ban.by.fetch()
        by_tid = by_user.tid if by_user else 0

        banned_chat_tids = []
        for chat_link in ban.banned_chats or []:
            chat = await chat_link.fetch()
            if chat:
                banned_chat_tids.append(chat.tid)

        result.append(
            FederationBanResponse(
                ban_iid=_require_ban_id(ban),
                user_id=ban.user_id,
                banned_chats=banned_chat_tids,
                time=ban.time,
                by=by_tid,
                reason=ban.reason,
                origin_fed=ban.origin_fed,
            )
        )
    return result


@router.post("/{fed_id}/bans", response_model=FederationBanResponse)
async def ban_user(
    fed_id: str,
    payload: FederationBanCreate,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> FederationBanResponse:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_admin(federation, user)

    target_user = await ChatModel.get_by_tid(payload.user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    ban = await FederationBanService.ban_user(federation, payload.user_id, user.iid, payload.reason)

    by_user = await ban.by.fetch()
    by_tid = by_user.tid if by_user else 0

    banned_chat_tids = []
    for chat_link in ban.banned_chats or []:
        chat = await chat_link.fetch()
        if chat:
            banned_chat_tids.append(chat.tid)

    return FederationBanResponse(
        ban_iid=_require_ban_id(ban),
        user_id=payload.user_id,
        banned_chats=banned_chat_tids,
        time=ban.time,
        by=by_tid,
        reason=ban.reason,
        origin_fed=ban.origin_fed,
    )


@router.delete("/{fed_id}/bans/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unban_user(
    fed_id: str,
    user_id: int,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_admin(federation, user)

    success, ban_info = await FederationBanService.unban_user(fed_id, user_id)
    if not success and ban_info:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ban originated from subscription")
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ban not found")
