from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, Field


class FilterActionPayload(BaseModel):
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class FilterActionResponse(FilterActionPayload):
    icon: str | None = None
    title: str | None = None
    description: str | None = None


class FilterResponse(BaseModel):
    id: PydanticObjectId
    handler: str
    version: int
    actions: list[FilterActionResponse]
    time: Any | None = None


class FiltersResponse(BaseModel):
    filters: list[FilterResponse]


class FilterCreate(BaseModel):
    handler: str
    actions: list[FilterActionPayload] = Field(min_length=1)


class FilterUpdate(BaseModel):
    handler: str | None = None
    actions: list[FilterActionPayload] | None = None


class FilterActionCatalogItem(BaseModel):
    name: str
    icon: str
    title: str
    as_filter: bool
    as_button: bool
    as_flood: bool
    allow_warns: bool
    has_interactive_setup: bool
    data_schema: dict[str, Any] | None = None
    default_data: dict[str, Any] | None = None


class FilterCatalogLimits(BaseModel):
    max_actions_per_filter: int
    max_ai_filters_per_chat: int


class FilterActionsCatalogResponse(BaseModel):
    limits: FilterCatalogLimits
    actions: list[FilterActionCatalogItem]
