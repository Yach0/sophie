from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator

PROTOCOL_VERSION = 1
SCHEMA_VERSION = 1
TELEMETRY_FRAME_LIMIT = 64 * 1024
CONTROL_FRAME_LIMIT = 2 * 1024 * 1024
REPLY_DATA_LIMIT = 1024 * 1024
MAX_OUTSTANDING_REQUESTS = 32
MAX_PENDING_CONTROL_BYTES = 4 * 1024 * 1024
SAFE_INTEGER_MAX = 2**53 - 1
MAX_EVENT_SUMMARY_LENGTH = 32 * 1024


class Category(StrEnum):
    MONGO = "mongo"
    REDIS = "redis"
    TELEGRAM = "telegram"
    AI_CACHE = "ai_cache"
    MIDDLEWARE = "middleware"
    RESOURCE = "resource"
    LOG = "log"
    PROCESS = "process"


class Phase(StrEnum):
    START = "start"
    FINISH = "finish"
    INSTANT = "instant"


class Level(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Origin(StrEnum):
    STARTUP = "startup"
    UPDATE = "update"
    BACKGROUND = "background"
    DEBUGGER = "debugger"


class Outcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapturedEvent(StrictModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    monotonic_ns: str = Field(pattern=r"^\d+$")
    category: Category
    name: str = Field(min_length=1, max_length=256)
    phase: Phase
    level: Level = Level.INFO
    trace_id: str | None = Field(default=None, max_length=64)
    span_id: str | None = Field(default=None, max_length=64)
    parent_span_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    update_id: int | None = Field(default=None, ge=0, le=SAFE_INTEGER_MAX)
    chat_tid: int | None = Field(default=None, ge=-SAFE_INTEGER_MAX, le=SAFE_INTEGER_MAX)
    duration_ms: float | None = Field(default=None, ge=0)
    error_type: str | None = Field(default=None, max_length=256)
    origin: Origin
    outcome: Outcome | None = None
    summary: str = Field(max_length=MAX_EVENT_SUMMARY_LENGTH)
    payload: JsonValue = None
    truncated: bool = False
    redacted: bool = False

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Event timestamps must include a UTC offset")
        return value.astimezone(UTC)


class StoredEvent(CapturedEvent):
    schema_version: Literal[1] = SCHEMA_VERSION
    seq: int = Field(gt=0, le=SAFE_INTEGER_MAX)
    session_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    pid: int = Field(gt=0, le=SAFE_INTEGER_MAX)


class EventSummary(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    seq: int
    session_id: str
    run_id: str
    pid: int
    timestamp: datetime
    monotonic_ns: str
    category: Category
    name: str
    phase: Phase
    level: Level
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    task_id: str | None
    update_id: int | None
    chat_tid: int | None
    duration_ms: float | None
    error_type: str | None
    origin: Origin
    outcome: Outcome | None
    summary: str
    truncated: bool
    redacted: bool

    @classmethod
    def from_event(cls, event: StoredEvent) -> EventSummary:
        return cls.model_validate(event.model_dump(exclude={"payload"}))


class EventFrame(StrictModel):
    protocol_version: Literal[1] = PROTOCOL_VERSION
    type: Literal["event"] = "event"
    run_id: str
    pid: int
    event: CapturedEvent


type WorkerState = Literal["starting", "ready", "reloading", "failed", "stopped"]


class StatusFrame(StrictModel):
    protocol_version: Literal[1] = PROTOCOL_VERSION
    type: Literal["status"] = "status"
    run_id: str
    pid: int
    state: WorkerState
    dropped_total: int = 0
    recorder_errors: int = 0
    detail: str | None = None


class CommandFrame(StrictModel):
    protocol_version: Literal[1] = PROTOCOL_VERSION
    type: Literal["command"] = "command"
    request_id: str
    run_id: str
    operation: str
    payload: dict[str, JsonValue]


class ProtocolErrorBody(StrictModel):
    code: str
    message: str
    ambiguous: bool = False


class ReplyFrame(StrictModel):
    protocol_version: Literal[1] = PROTOCOL_VERSION
    type: Literal["reply"] = "reply"
    request_id: str
    run_id: str
    result: JsonValue = None
    error: ProtocolErrorBody | None = None


WireFrame = Annotated[EventFrame | StatusFrame | CommandFrame | ReplyFrame, Field(discriminator="type")]
WIRE_FRAME_ADAPTER = TypeAdapter(WireFrame)


class FrameError(ValueError):
    pass


def strict_json_loads(data: bytes | str) -> JsonValue:
    def reject_constant(value: str) -> None:
        raise FrameError(f"Non-finite JSON number is not allowed: {value}")

    def parse_integer(value: str) -> int:
        parsed = int(value)
        if abs(parsed) > SAFE_INTEGER_MAX:
            raise FrameError("Unsafe bare JSON integer must use canonical Extended JSON")
        return parsed

    def parse_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise FrameError("Non-finite JSON number is not allowed")
        return parsed

    try:
        return json.loads(
            data,
            parse_constant=reject_constant,
            parse_int=parse_integer,
            parse_float=parse_float,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise FrameError("Malformed JSON frame") from error


def validate_reply_data_size(value: JsonValue) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise FrameError("Reply data is not strict JSON") from error
    if len(encoded) > REPLY_DATA_LIMIT:
        raise FrameError(f"Reply data exceeds {REPLY_DATA_LIMIT} bytes")


def encode_frame(frame: BaseModel, limit: int) -> bytes:
    try:
        encoded = (
            json.dumps(
                frame.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as error:
        raise FrameError("Frame is not strict JSON") from error
    if len(encoded) > limit:
        raise FrameError(f"Encoded frame exceeds {limit} bytes")
    return encoded


async def read_frame(reader: asyncio.StreamReader, limit: int) -> WireFrame:
    try:
        encoded = await reader.readline()
    except (asyncio.LimitOverrunError, ValueError) as error:
        raise FrameError(f"Frame exceeds {limit} bytes") from error
    if not encoded:
        raise EOFError
    if not encoded.endswith(b"\n"):
        raise FrameError("Frame ended before newline")
    if len(encoded) > limit:
        raise FrameError(f"Frame exceeds {limit} bytes")
    decoded = strict_json_loads(encoded)
    try:
        return WIRE_FRAME_ADAPTER.validate_python(decoded)
    except ValueError as error:
        raise FrameError("Frame does not match protocol schema") from error
