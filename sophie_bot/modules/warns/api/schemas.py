from pydantic import BaseModel, Field


class WarnResponse(BaseModel):
    id: str
    user_id: int
    admin_id: int | None = None
    reason: str | None
    date: str


class WarnSettingsResponse(BaseModel):
    max_warns: int
    actions: list[dict]
    on_each_warn_actions: list[dict]
    on_max_warn_actions: list[dict]


class WarnSettingsUpdate(BaseModel):
    max_warns: int | None = Field(None, ge=2, le=10000)
