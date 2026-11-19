from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, Literal
from uuid import uuid4

from beanie import Document
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import IndexModel

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel

MemoryLayer = Literal["personal", "observation", "event", "norm"]
MemoryKind = Literal["fact", "preference", "proposal", "commitment", "decision", "question", "joke", "disputed"]


class MemorySource(BaseModel):
    message_id: int
    speaker_tid: int
    speaker_name: str = ""
    username: str | None = None
    text: str
    sent_at: datetime
    topic_id: int | None = None
    reply_to_message_id: int | None = None
    branch_id: int
    processed: bool = False
    writer_attempts: int = 0

    @field_validator("sent_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MemoryFact(BaseModel):
    fact_id: str = Field(default_factory=lambda: uuid4().hex)
    speaker_tid: int
    subject_tids: list[int]
    scope: Literal["person", "group"]
    layer: MemoryLayer
    kind: MemoryKind
    event: str
    text: str
    topic_id: int | None
    branch_ids: list[int]
    evidence_ids: list[int]
    recorded_at: datetime
    occurred_at: datetime
    previous_id: str | None = None
    editor_tid: int | None = None

    @field_validator("recorded_at", "occurred_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class StoredMemoryState(BaseModel):
    facts: list[MemoryFact] = Field(default_factory=list)
    excluded_users: list[int] = Field(default_factory=list)
    generation: str = Field(default_factory=lambda: uuid4().hex)
    processed_ids: list[int] = Field(default_factory=list)
    writer_attempts: dict[str, int] = Field(default_factory=dict)
    pending_context_reset: bool = False


class MemoryState(StoredMemoryState):
    """Working view: original messages are hydrated from Redis, never stored in MongoDB."""

    sources: list[MemorySource] = Field(default_factory=list)


class MemoryAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ADD", "UPDATE", "NOOP"]
    target_id: str | None = None
    speaker_tid: int | None = None
    subject_tids: list[int] = Field(default_factory=list, max_length=30)
    scope: Literal["person", "group"] = "person"
    layer: MemoryLayer = "personal"
    kind: MemoryKind = "fact"
    event: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=600)
    evidence_ids: list[int] = Field(default_factory=list, max_length=20)


class MemoryActions(BaseModel):
    actions: list[MemoryAction] = Field(default_factory=list, max_length=60)


class AIEvidenceMemoryModel(Document):
    chat: Link[ChatModel]
    state: StoredMemoryState = Field(default_factory=StoredMemoryState)

    class Settings:
        name = "ai_evidence_memory"
        use_revision = True
        indexes: ClassVar[list[IndexModel]] = [IndexModel([("chat.$id", 1)], unique=True)]


class AIMemoryRetentionPolicy(Document):
    chat_tid: int
    days: int = Field(ge=1, le=365)

    class Settings:
        name = "ai_memory_retention_policy"
        indexes: ClassVar[list[IndexModel]] = [IndexModel("chat_tid", unique=True)]
