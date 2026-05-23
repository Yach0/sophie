from __future__ import annotations

from random import choice
from string import printable
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError
from regex import regex

from sophie_bot.constants import AI_FILTER_LIMIT_PER_CHAT, FILTERS_MAX_TRIGGERS
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.filters.utils_.handle_action import get_effective_filter_actions
from sophie_bot.modules.locks.utils.conflicts import get_lock_type_owner
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type

from .schemas import FilterActionCatalogItem, FilterActionPayload, FilterActionResponse, FilterResponse

MAX_FILTER_ACTIONS = FILTERS_MAX_TRIGGERS + 1


async def get_chat_or_404(chat_iid: PydanticObjectId) -> ChatModel:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


def _normalize_action_data(action_name: str, action_data: dict[str, Any]) -> dict[str, Any]:
    action = ALL_MODERN_ACTIONS.get(action_name)
    if not action:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid action name: {action_name}",
        )
    if not action.as_filter:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Action '{action_name}' cannot be used as a filter action",
        )

    if not action.data_object:
        return action_data

    payload = action_data
    if not payload and action.default_data is not None:
        return action.default_data.model_dump(mode="json")

    try:
        validated_data = action.data_object(**payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid action data for '{action_name}': {exc}",
        ) from exc

    return validated_data.model_dump(mode="json")


def validate_filter_actions(actions: list[FilterActionPayload]) -> dict[str, dict[str, Any]]:
    if not actions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filter actions cannot be empty")
    if len(actions) > MAX_FILTER_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many filter actions, maximum is {MAX_FILTER_ACTIONS}",
        )

    validated_actions: dict[str, dict[str, Any]] = {}
    for action_payload in actions:
        if action_payload.name in validated_actions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate action name: {action_payload.name}",
            )

        validated_actions[action_payload.name] = _normalize_action_data(action_payload.name, action_payload.data)

    return validated_actions


async def validate_filter_handler(
    chat_iid: PydanticObjectId,
    handler: str,
    exclude_filter_id: PydanticObjectId | None = None,
) -> None:
    normalized_handler = handler.strip()
    if not normalized_handler:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filter handler cannot be empty")

    if is_supported_lock_type(normalized_handler):
        existing_lock_owner = await get_lock_type_owner(chat_iid, normalized_handler)
        if existing_lock_owner == "locks":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Lock type '{normalized_handler}' is already enforced by the Locks module",
            )

    existing_filter = await FiltersModel.get_by_keyword(chat_iid, normalized_handler)
    if existing_filter and existing_filter.id != exclude_filter_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Filter with handler '{normalized_handler}' already exists",
        )

    if normalized_handler.startswith("ai:"):
        prompt = normalized_handler[3:].strip()
        if not prompt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI filter prompt cannot be empty",
            )

        is_editing_ai_filter = False
        if exclude_filter_id is not None:
            existing_by_id = await FiltersModel.get_by_id(exclude_filter_id)
            if existing_by_id and existing_by_id.handler.startswith("ai:"):
                is_editing_ai_filter = True

        if not is_editing_ai_filter:
            current_ai_filters_count = await FiltersModel.count_ai_filters(chat_iid)
            if current_ai_filters_count >= AI_FILTER_LIMIT_PER_CHAT:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Maximum number of AI filters reached ({AI_FILTER_LIMIT_PER_CHAT} per chat)",
                )

    if normalized_handler.startswith("re:"):
        random_text = "".join(choice(printable) for _index in range(50))
        try:
            regex.match(normalized_handler[3:], random_text, timeout=0.2)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provided regex pattern is too slow to execute",
            ) from exc


def build_filter_action_response(action_name: str, action_data: dict[str, Any]) -> FilterActionResponse:
    action = ALL_MODERN_ACTIONS.get(action_name)
    if not action:
        return FilterActionResponse(name=action_name, data=action_data)

    normalized_data = _normalize_action_data(action_name, action_data)
    validated_data: BaseModel | None = None
    if action.data_object:
        validated_data = action.data_object(**normalized_data)

    description = str(action.description(validated_data))

    return FilterActionResponse(
        name=action_name,
        data=normalized_data,
        icon=action.icon,
        title=str(action.title),
        description=description,
    )


def build_filter_response(filter_item: FiltersModel) -> FilterResponse:
    if filter_item.id is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Filter ID is missing")

    return FilterResponse(
        id=filter_item.id,
        handler=filter_item.handler,
        version=filter_item.effective_version,
        actions=[
            build_filter_action_response(action.name, action.data or {})
            for action in get_effective_filter_actions(filter_item)
        ],
        time=filter_item.time,
    )


def build_filter_action_catalog() -> list[FilterActionCatalogItem]:
    catalog_items: list[FilterActionCatalogItem] = []

    for action_name, action in sorted(ALL_MODERN_ACTIONS.items()):
        default_data = action.default_data.model_dump(mode="json") if action.default_data is not None else None
        data_schema = action.data_object.model_json_schema() if action.data_object else None

        catalog_items.append(
            FilterActionCatalogItem(
                name=action_name,
                icon=action.icon,
                title=str(action.title),
                as_filter=action.as_filter,
                as_button=action.as_button,
                as_flood=action.as_flood,
                allow_warns=action.allow_warns,
                has_interactive_setup=action.interactive_setup is not None,
                data_schema=data_schema,
                default_data=default_data,
            )
        )

    return catalog_items
