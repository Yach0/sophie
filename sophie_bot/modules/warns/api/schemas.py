from typing import Optional

from pydantic import BaseModel, Field


class WarnResponse(BaseModel):
    id: str
    user_id: int
    admin_id: int
    reason: Optional[str]
    date: str


class WarnSettingsResponse(BaseModel):
    max_warns: int
    actions: list[dict]
    on_each_warn_actions: list[dict]
    on_max_warn_actions: list[dict]


class WarnSettingsUpdate(BaseModel):
    max_warns: Optional[int] = Field(None, ge=2, le=10000)
