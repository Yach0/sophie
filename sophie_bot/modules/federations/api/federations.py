from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from beanie.odm.operators.find.comparison import In
from fastapi import APIRouter, Depends, HTTPException, status

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation, FederationBan
from sophie_bot.modules.federations.services import (
    FederationManageService,
    FederationBanService,
    FederationChatService,
)
from sophie_bot.modules.federations.services.permissions import FederationPermissionService
from sophie_bot.utils.api.auth import get_current_user, rest_require_admin
from sophie_bot.utils.feature_flags import is_enabled

from .dependencies import require_federations_rest_api
from .schemas import (
    FederationBanCreate,
    FederationBanResponse,
    FederationChatAdd,
    FederationChatResponse,
    FederationCreate,
    FederationDetailResponse,
    FederationLogChannelUpdate,
    FederationSubscriptionAdd,
    FederationSubscriptionResponse,
    FederationSummaryResponse,
    FederationUpdate,
)


async def require_federations_feature_flag():
    """Require new_feds feature flag to be enabled."""
    if not await is_enabled("new_feds"):
        raise HTTPException(status_code=503, detail="Federations feature is disabled")


router = APIRouter(
    prefix="/federations",
    tags=["federations"],
    dependencies=[Depends(require_federations_rest_api), Depends(require_federations_feature_flag)],
)


async def _resolve_chat_iids(chat_tids: list[int]) -> list[PydanticObjectId]:
    if not chat_tids:
        return []
    chat_models = await ChatModel.find(In(ChatModel.tid, chat_tids)).to_list()
    return [chat_model.iid for chat_model in chat_models]


async def _resolve_log_chat_iid(log_chat_tid: int | None) -> PydanticObjectId | None:
    if not log_chat_tid:
        return None
    chat_model = await ChatModel.get_by_tid(log_chat_tid)
    if not chat_model:
        return None
    return chat_model.iid


async def _resolve_creator_iid(creator_tid: int) -> PydanticObjectId | None:
    creator = await ChatModel.get_by_tid(creator_tid)
    if not creator:
        return None
    return creator.id


async def _batch_resolve_federations(federations: list[Federation]) -> dict[str, dict]:
    """Batch resolve federation-related data to avoid N+1 queries."""
    # With Links, we don't need batch resolution if we only need IIDs.
    return {}


def _federation_summary(federation: Federation, batch_data: dict) -> FederationSummaryResponse:
    return FederationSummaryResponse(
        fed_id=federation.fed_id,
        fed_name=federation.fed_name,
        creator_iid=federation.creator.id,
        log_chat_iid=federation.log_chat.iid if federation.log_chat else None,
    )


def _federation_detail(federation: Federation, batch_data: dict) -> FederationDetailResponse:
    chat_iids = [c.iid for c in federation.chats] if federation.chats else []

    return FederationDetailResponse(
        fed_id=federation.fed_id,
        fed_name=federation.fed_name,
        creator_iid=federation.creator.id,
        log_chat_iid=federation.log_chat.iid if federation.log_chat else None,
        chat_iids=chat_iids,
        subscribed_fed_ids=federation.subscribed or [],
    )


async def _require_federation_access(federation: Federation, user: ChatModel) -> None:
    if user.tid == CONFIG.owner_id:
        return
    if await FederationPermissionService.is_federation_owner(federation, user.tid):
        return
    if await FederationPermissionService.is_federation_admin(federation, user.tid):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this federation")


async def _require_federation_owner(federation: Federation, user: ChatModel) -> None:
    if user.tid == CONFIG.owner_id:
        return
    if not await FederationPermissionService.is_federation_owner(federation, user.tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only federation owners can perform this action"
        )


async def _require_federation_admin(federation: Federation, user: ChatModel) -> None:
    if user.tid == CONFIG.owner_id:
        return
    if not await FederationPermissionService.is_federation_admin(federation, user.tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only federation admins can perform this action"
        )


def _require_ban_id(federation_ban: FederationBan) -> PydanticObjectId:
    if not federation_ban.id:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ban ID missing")
    return federation_ban.id


@router.get("", response_model=list[FederationSummaryResponse])
async def list_federations(
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> list[FederationSummaryResponse]:
    owned_federations = await Federation.find(Federation.creator.id == user.iid).to_list()
    admin_federations = await Federation.find(Federation.admins == user.iid).to_list()

    unique_federations: dict[str, Federation] = {federation.fed_id: federation for federation in owned_federations}
    for federation in admin_federations:
        unique_federations.setdefault(federation.fed_id, federation)

    # Batch resolve all related data
    federations_list = list(unique_federations.values())
    if not federations_list:
        return []

    batch_data = await _batch_resolve_federations(federations_list)

    return [_federation_summary(federation, batch_data) for federation in federations_list]


@router.post("", response_model=FederationSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_federation(
    payload: FederationCreate,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> FederationSummaryResponse:
    federation = await FederationManageService.create_federation(payload.name, user.iid)
    if not federation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to create federation")

    batch_data = await _batch_resolve_federations([federation])
    return _federation_summary(federation, batch_data)


@router.get("/{fed_id}", response_model=FederationDetailResponse)
async def get_federation(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> FederationDetailResponse:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")

    await _require_federation_access(federation, user)

    batch_data = await _batch_resolve_federations([federation])
    return _federation_detail(federation, batch_data)


@router.patch("/{fed_id}", response_model=FederationSummaryResponse)
async def update_federation(
    fed_id: str,
    payload: FederationUpdate,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> FederationSummaryResponse:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    updates: dict[str, str] = {}
    if payload.name:
        updates["fed_name"] = payload.name

    if updates:
        await FederationManageService.update_federation(federation, updates)

    batch_data = await _batch_resolve_federations([federation])
    return _federation_summary(federation, batch_data)


@router.delete("/{fed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_federation(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)
    await FederationManageService.delete_federation(federation)


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

    chats = await ChatModel.find(In(ChatModel.iid, [c.to_ref() for c in federation.chats])).to_list()
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

    await FederationChatService.add_chat_to_federation(federation, chat.iid)


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

    await FederationChatService.remove_chat_from_federation(federation, chat.iid)


@router.get("/{fed_id}/subscriptions", response_model=list[FederationSubscriptionResponse])
async def list_federation_subscriptions(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> list[FederationSubscriptionResponse]:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    _require_federation_access(federation, user)

    subscription_ids = federation.subscribed or []
    if not subscription_ids:
        return []

    subscriptions = await Federation.find(In(Federation.fed_id, subscription_ids)).to_list()
    return [
        FederationSubscriptionResponse(fed_id=sub_federation.fed_id, fed_name=sub_federation.fed_name)
        for sub_federation in subscriptions
    ]


@router.post("/{fed_id}/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe_federation(
    fed_id: str,
    payload: FederationSubscriptionAdd,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    success = await FederationManageService.subscribe_to_federation(federation, payload.target_fed_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Subscription failed")


@router.delete("/{fed_id}/subscriptions/{target_fed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_federation(
    fed_id: str,
    target_fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    success = await FederationManageService.unsubscribe_from_federation(federation, target_fed_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unsubscription failed")


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
        # Fetch by user to get tid
        by_user = await ban.by.fetch()
        by_tid = by_user.tid if by_user else 0

        # Fetch banned chats to get tids
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

    # Fetch by user to get tid
    by_user = await ban.by.fetch()
    by_tid = by_user.tid if by_user else 0

    # Fetch banned chats to get tids
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


@router.post("/{fed_id}/log_channel", status_code=status.HTTP_204_NO_CONTENT)
async def set_federation_log_channel(
    fed_id: str,
    payload: FederationLogChannelUpdate,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    if federation.log_chat:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Log channel already set")

    chat = await ChatModel.get_by_iid(payload.chat_iid)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    await FederationManageService.set_federation_log_channel(federation, chat.iid)


@router.delete("/{fed_id}/log_channel", status_code=status.HTTP_204_NO_CONTENT)
async def unset_federation_log_channel(
    fed_id: str,
    user: Annotated[ChatModel, Depends(get_current_user)],
) -> None:
    federation = await FederationManageService.get_federation_by_id(fed_id)
    if not federation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Federation not found")
    await _require_federation_owner(federation, user)

    if not federation.log_chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log channel not set")

    await FederationManageService.remove_federation_log_channel(federation)
