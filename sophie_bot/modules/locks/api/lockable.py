from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.locks.handlers.lockable import (
    CONTENT_TYPES,
    ENTITY_TYPES,
    FORWARD_TYPES,
    LOCK_TYPE_DESCRIPTIONS,
    SPECIAL_TYPES,
    STICKER_PACK_TYPES,
    SUPPORTED_LANGUAGES,
    TEXT_PATTERN_TYPES,
)
from sophie_bot.utils.api.auth import get_current_user

from .schemas import LockableItem, LockableResponse

router = APIRouter()


def _build_lockable_items(lock_types: tuple[str, ...]) -> list[LockableItem]:
    items: list[LockableItem] = []
    for lock_type in lock_types:
        description = LOCK_TYPE_DESCRIPTIONS.get(lock_type, lock_type)
        items.append(
            LockableItem(
                type=lock_type,
                description=str(description),
            )
        )
    return items


@router.get("/lockable", response_model=LockableResponse)
async def get_lockable_types(
    user: Annotated[ChatModel, Depends(get_current_user)],
):
    return LockableResponse(
        content_types=_build_lockable_items(CONTENT_TYPES),
        entity_types=_build_lockable_items(ENTITY_TYPES),
        forward_types=_build_lockable_items(FORWARD_TYPES),
        text_pattern_types=_build_lockable_items(TEXT_PATTERN_TYPES),
        sticker_types=_build_lockable_items(STICKER_PACK_TYPES),
        special_types=_build_lockable_items(SPECIAL_TYPES),
        supported_languages=SUPPORTED_LANGUAGES,
    )
