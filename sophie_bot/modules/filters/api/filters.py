from __future__ import annotations

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Response, status

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.services.rest import ServicesDep
from sophie_bot.utils.api.auth import rest_require_admin

from .dependencies import require_filters_feature
from .schemas import FilterCreate, FilterResponse, FiltersResponse, FilterUpdate
from .utils import build_filter_response, get_chat_or_404, validate_filter_actions, validate_filter_handler

router = APIRouter(
    prefix="/filters",
    tags=["filters"],
    dependencies=[Depends(require_filters_feature)],
)


@router.get("/{chat_iid}", response_model=FiltersResponse)
async def list_filters(
    chat_iid: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin())],
    services: ServicesDep,
) -> FiltersResponse:
    _ = user
    await get_chat_or_404(chat_iid)
    filter_items = await FiltersModel.get_filters(chat_iid) or []
    return FiltersResponse(
        filters=[
            build_filter_response(
                filter_item,
                services.modules.actions,
                services.modules.action_handlers,
            )
            for filter_item in filter_items
        ]
    )


@router.get("/{chat_iid}/{filter_id}", response_model=FilterResponse)
async def get_filter(
    chat_iid: PydanticObjectId,
    filter_id: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin())],
    services: ServicesDep,
) -> FilterResponse:
    _ = user
    chat = await get_chat_or_404(chat_iid)
    filter_item = await FiltersModel.get_by_id(filter_id)
    if not filter_item or filter_item.chat.id != chat.iid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

    return build_filter_response(
        filter_item,
        services.modules.actions,
        services.modules.action_handlers,
    )


@router.post("/{chat_iid}", response_model=FilterResponse, status_code=status.HTTP_201_CREATED)
async def create_filter(
    chat_iid: PydanticObjectId,
    payload: FilterCreate,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
    services: ServicesDep,
) -> FilterResponse:
    chat = await get_chat_or_404(chat_iid)
    await validate_filter_handler(chat.iid, payload.handler)
    validated_actions = validate_filter_actions(
        payload.actions,
        services.modules.actions,
    )

    filter_item = FiltersModel(
        chat=chat.iid,
        handler=payload.handler.strip(),
        version=2,
        action=None,
        actions=validated_actions,
    )
    await filter_item.insert()
    await log_event(chat.tid, user.tid, LogEvent.FILTER_SAVED, {"keyword": filter_item.handler})

    return build_filter_response(
        filter_item,
        services.modules.actions,
        services.modules.action_handlers,
    )


@router.patch("/{chat_iid}/{filter_id}", response_model=FilterResponse)
async def update_filter(
    chat_iid: PydanticObjectId,
    filter_id: PydanticObjectId,
    payload: FilterUpdate,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
    services: ServicesDep,
) -> FilterResponse:
    chat = await get_chat_or_404(chat_iid)
    filter_item = await FiltersModel.get_by_id(filter_id)
    if not filter_item or filter_item.chat.id != chat.iid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

    if payload.handler is not None:
        await validate_filter_handler(chat.iid, payload.handler, exclude_filter_id=filter_id)
        filter_item.handler = payload.handler.strip()

    if payload.actions is not None:
        filter_item.actions = validate_filter_actions(
            payload.actions,
            services.modules.actions,
        )
        filter_item.action = None
        filter_item.version = 2

    await filter_item.save()
    await log_event(chat.tid, user.tid, LogEvent.FILTER_SAVED, {"keyword": filter_item.handler})

    return build_filter_response(
        filter_item,
        services.modules.actions,
        services.modules.action_handlers,
    )


@router.delete("/{chat_iid}/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(
    chat_iid: PydanticObjectId,
    filter_id: PydanticObjectId,
    user: Annotated[ChatModel, Depends(rest_require_admin(permission="can_change_info"))],
) -> Response:
    chat = await get_chat_or_404(chat_iid)
    filter_item = await FiltersModel.get_by_id(filter_id)
    if not filter_item or filter_item.chat.id != chat.iid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

    handler = filter_item.handler
    await filter_item.delete()
    await log_event(chat.tid, user.tid, LogEvent.FILTER_DELETED, {"keyword": handler})

    return Response(status_code=status.HTTP_204_NO_CONTENT)
