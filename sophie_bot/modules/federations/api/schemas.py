from __future__ import annotations

from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, Field


class FederationCreate(BaseModel):
    name: str = Field(min_length=1)


class FederationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)


class FederationSummaryResponse(BaseModel):
    fed_id: str
    fed_name: str
    creator_iid: PydanticObjectId | None
    log_chat_iid: PydanticObjectId | None


class FederationDetailResponse(FederationSummaryResponse):
    chat_iids: list[PydanticObjectId]
    subscribed_fed_ids: list[str]


class FederationChatResponse(BaseModel):
    chat_iid: PydanticObjectId
    title: str
    username: str | None


class FederationChatAdd(BaseModel):
    chat_iid: PydanticObjectId


class FederationSubscriptionAdd(BaseModel):
    target_fed_id: str


class FederationSubscriptionResponse(BaseModel):
    fed_id: str
    fed_name: str


class FederationBanCreate(BaseModel):
    user_id: int
    reason: str | None = None


class FederationBanResponse(BaseModel):
    ban_iid: PydanticObjectId
    user_id: int
    banned_chats: list[int]
    time: datetime
    by: int
    reason: str | None
    origin_fed: str | None


class FederationLogChannelUpdate(BaseModel):
    chat_iid: PydanticObjectId
