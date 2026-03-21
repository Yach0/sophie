from __future__ import annotations

from pydantic import BaseModel


class LocksResponse(BaseModel):
    locked: list[str]


class LocksPayload(BaseModel):
    locked: list[str]


class LockableItem(BaseModel):
    type: str
    description: str


class LockableResponse(BaseModel):
    content_types: list[LockableItem]
    entity_types: list[LockableItem]
    forward_types: list[LockableItem]
    text_pattern_types: list[LockableItem]
    sticker_types: list[LockableItem]
    special_types: list[LockableItem]
    supported_languages: dict[str, str]
