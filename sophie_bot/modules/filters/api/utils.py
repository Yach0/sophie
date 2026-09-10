from __future__ import annotations

from collections.abc import Mapping
from random import choice
from string import printable
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException, status
from regex import regex

from sophie_bot.constants import AI_FILTER_LIMIT_PER_CHAT, FILTER_MAX_ACTIONS
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.db.models.notes import CURRENT_SAVEABLE_VERSION, Saveable
from sophie_bot.modules.filters.utils_.handle_action import get_effective_filter_actions
from sophie_bot.modules.locks.utils.conflicts import get_lock_type_owner
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type
from sophie_bot.modules.notes.utils.rich import rich_message_to_html_fallback, validate_rich_message_api
from sophie_bot.shared.action_registry import normalize_action_data
from sophie_bot.shared.actions import (
    ActionDefinition,
    ActionValidationError,
    ModernActionABC,
)

from .schemas import FilterActionCatalogItem, FilterActionPayload, FilterActionResponse, FilterResponse


async def get_chat_or_404(chat_iid: PydanticObjectId) -> ChatModel:
    chat = await ChatModel.get_by_iid(chat_iid)
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


def _normalize_action_data(
    action_name: str,
    action_data: dict[str, Any],
    actions: Mapping[str, ActionDefinition[Any]],
) -> dict[str, Any]:
    try:
        normalized_data = normalize_action_data(
            actions,
            action_name,
            action_data,
            capability="filter",
        )
        definition = actions[action_name]
        if definition.data_object is None:
            return normalized_data

        validated_data = definition.data_object.model_validate(normalized_data)
        if isinstance(validated_data, Saveable) and validated_data.rich_message is not None:
            try:
                validate_rich_message_api(validated_data.rich_message)
                fallback = rich_message_to_html_fallback(validated_data.rich_message)
                if validated_data.text not in (None, "", fallback):
                    raise ValueError("text must match the Rich message fallback")
            except ValueError as validation_error:
                raise ActionValidationError(action_name, "data", str(validation_error)) from validation_error
            validated_data.text = fallback
            validated_data.file = None
            validated_data.files = []
            validated_data.version = CURRENT_SAVEABLE_VERSION
        return validated_data.model_dump(mode="json")
    except ActionValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.detail,
        ) from error


def validate_filter_actions(
    action_payloads: list[FilterActionPayload],
    actions: Mapping[str, ActionDefinition[Any]],
) -> dict[str, dict[str, Any]]:
    if not action_payloads:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filter actions cannot be empty")
    if len(action_payloads) > FILTER_MAX_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many filter actions, maximum is {FILTER_MAX_ACTIONS}",
        )

    validated_actions: dict[str, dict[str, Any]] = {}
    for action_payload in action_payloads:
        if action_payload.name in validated_actions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate action name: {action_payload.name}",
            )

        validated_actions[action_payload.name] = _normalize_action_data(
            action_payload.name,
            action_payload.data,
            actions,
        )

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


def build_filter_action_response(
    action_name: str,
    action_data: dict[str, Any],
    actions: Mapping[str, ActionDefinition[Any]],
    handlers: Mapping[str, ModernActionABC[Any]],
) -> FilterActionResponse:
    definition = actions.get(action_name)
    action = handlers.get(action_name)
    if definition is None or action is None:
        return FilterActionResponse(name=action_name, data=action_data)

    normalized_data = _normalize_action_data(action_name, action_data, actions)
    loaded_data = definition.load_data(normalized_data)
    description = str(action.description(loaded_data))

    return FilterActionResponse(
        name=action_name,
        data=normalized_data,
        icon=definition.icon,
        title=str(definition.title),
        description=description,
    )


def build_filter_response(
    filter_item: FiltersModel,
    actions: Mapping[str, ActionDefinition[Any]],
    handlers: Mapping[str, ModernActionABC[Any]],
) -> FilterResponse:
    if filter_item.id is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Filter ID is missing")

    return FilterResponse(
        id=filter_item.id,
        handler=filter_item.handler,
        version=filter_item.effective_version,
        actions=[
            build_filter_action_response(
                action.name,
                action.data or {},
                actions,
                handlers,
            )
            for action in get_effective_filter_actions(filter_item)
        ],
        time=filter_item.time,
    )


def build_filter_action_catalog(
    actions: Mapping[str, ActionDefinition[Any]],
) -> list[FilterActionCatalogItem]:
    catalog_items: list[FilterActionCatalogItem] = []

    for action_name, action in sorted(actions.items()):
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
                has_interactive_setup=action.has_interactive_setup,
                data_schema=data_schema,
                default_data=default_data,
            )
        )

    return catalog_items
