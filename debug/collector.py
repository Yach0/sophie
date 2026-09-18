from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import math
import os
import re
import secrets
import uuid
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import InvalidOperation
from typing import Annotated, Any, Literal

from bson import ObjectId, json_util
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, field_validator, model_validator

from debug.capture import REDACTED, TRUNCATED, build_event, normalize_payload
from debug.i18n import gettext_debug as _
from debug.protocol import (
    SAFE_INTEGER_MAX,
    CapturedEvent,
    Category,
    EventSummary,
    FrameError,
    Level,
    Origin,
    Outcome,
    Phase,
    ReplyFrame,
    StoredEvent,
    strict_json_loads,
)

MAX_EVENTS = 10_000
MAX_EVENT_BYTES = 64 * 1024 * 1024
MAX_RESOURCE_SAMPLES = 300
MAX_QUERY_ITEMS = 200
MAX_PREPARATIONS = 100
MAX_ACTION_HISTORY = 1_000
MAX_PREPARATION_BYTES = 64 * 1024
PREPARATION_TTL_SECONDS = 60
READ_DEADLINE_SECONDS = 5
WRITE_REPLY_DEADLINE_SECONDS = 5
MAX_HTTP_JSON_BYTES = 2 * 1024 * 1024
SESSION_COOKIE = "sophie_debug_session"
PLACEHOLDERS = frozenset({REDACTED, TRUNCATED})
SERVER_CODE_OPERATORS = frozenset({"$where", "$function", "$accumulator"})
TERMINAL_ACTION_STATES = frozenset({"succeeded", "failed", "expired"})
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorDetail(ApiModel):
    code: str
    message: str
    request_id: str
    status_url: str | None = None


class ApiError(ApiModel):
    error: ApiErrorDetail


class SessionResponse(ApiModel):
    session_id: str
    run_id: str | None
    state: Literal["starting", "ready", "reloading", "failed", "stopped"]
    restart_required: bool
    worker_pid: int | None
    sanitized_targets: dict[str, str | int]
    capabilities: dict[str, bool | str | int]
    dropped_total: int
    recorder_errors: int
    oldest_seq: int | None
    latest_seq: int | None
    event_count: int
    event_bytes: int
    limits: dict[str, int]
    csrf_token: str
    sensitive: Literal[True] = True
    detail: str | None


class EventsResponse(ApiModel):
    events: list[EventSummary]
    next_after: int
    oldest_seq: int | None
    latest_seq: int | None
    gap: bool
    dropped_total: int


class ResourceResponse(ApiModel):
    run_id: str | None
    worker: list[dict[str, Any]]
    collector: list[dict[str, Any]]
    vite: list[dict[str, Any]]


class MongoCollectionsResponse(ApiModel):
    collections: list[str]
    run_id: str


class MongoQueryRequest(ApiModel):
    collection: str = Field(min_length=1, max_length=255)
    filter: dict[str, JsonValue] = Field(default_factory=dict)
    projection: dict[str, Literal[0, 1]] | None = None
    sort: list[tuple[str, Literal[-1, 1]]] = Field(default_factory=list, max_length=32)
    skip: StrictInt = Field(default=0, ge=0, le=1_000_000)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_ITEMS)

    @field_validator("projection", mode="before")
    @classmethod
    def validate_projection_numbers(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(isinstance(item, bool) for item in value.values()):
            raise ValueError("projection values must be integer 0 or 1")
        return value

    @field_validator("sort", mode="before")
    @classmethod
    def validate_sort_numbers(cls, value: Any) -> Any:
        if isinstance(value, list) and any(
            isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], bool) for item in value
        ):
            raise ValueError("sort directions must be integer -1 or 1")
        return value

    @model_validator(mode="after")
    def validate_query(self) -> MongoQueryRequest:
        _validate_collection(self.collection)
        _reject_unsafe_integers(self.filter)
        _reject_server_code(self.filter)
        if self.projection is not None:
            for value in self.projection.values():
                if isinstance(value, bool):
                    raise ValueError("projection values must be integer 0 or 1")  # noqa: TRY004
        if any(isinstance(direction, bool) for _field, direction in self.sort):
            raise ValueError("sort directions must be integer -1 or 1")
        return self


class MongoQueryResponse(ApiModel):
    items: list[JsonValue]
    has_more: bool
    duration_ms: float
    truncated: bool
    run_id: str


class Base64RedisBytes(ApiModel):
    base64: str = Field(max_length=64 * 1024)

    @field_validator("base64")
    @classmethod
    def validate_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid base64") from error
        return value


type RedisBytes = str | Base64RedisBytes


class RedisScanQuery(ApiModel):
    op: Literal["scan"]
    pattern: RedisBytes = "*"
    cursor: StrictInt = Field(default=0, ge=0, le=SAFE_INTEGER_MAX)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_ITEMS)


class RedisStringQuery(ApiModel):
    op: Literal["string"]
    key: RedisBytes
    offset: StrictInt = Field(default=0, ge=0, le=SAFE_INTEGER_MAX)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_ITEMS)


class RedisHashQuery(ApiModel):
    op: Literal["hash"]
    key: RedisBytes
    cursor: StrictInt = Field(default=0, ge=0, le=SAFE_INTEGER_MAX)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_ITEMS)


class RedisSetQuery(ApiModel):
    op: Literal["set"]
    key: RedisBytes
    cursor: StrictInt = Field(default=0, ge=0, le=SAFE_INTEGER_MAX)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_ITEMS)


class RedisListQuery(ApiModel):
    op: Literal["list"]
    key: RedisBytes
    offset: StrictInt = Field(default=0, ge=0, le=SAFE_INTEGER_MAX)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_ITEMS)


class RedisZsetQuery(ApiModel):
    op: Literal["zset"]
    key: RedisBytes
    offset: StrictInt = Field(default=0, ge=0, le=SAFE_INTEGER_MAX)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_ITEMS)


RedisQueryRequest = Annotated[
    RedisScanQuery | RedisStringQuery | RedisHashQuery | RedisSetQuery | RedisListQuery | RedisZsetQuery,
    Field(discriminator="op"),
]


class RedisQueryResponse(ApiModel):
    type: str
    ttl: int | None
    data: JsonValue
    next_cursor: int = Field(
        description=_("Redis cursor scans are live, non-atomic views; concurrent changes may move entries.")
    )
    has_more: bool
    truncated: bool
    duration_ms: float
    run_id: str
    length: int | None = None


class AiCacheResponse(ApiModel):
    kind: Literal["messages", "tools"]
    key: str
    ttl: int
    configured_ttl_seconds: int
    count: int
    entries: list[JsonValue]
    invalid_entries: list[JsonValue]
    truncated: bool
    run_id: str
    offset: int | None = None
    next_cursor: int | None = None
    has_more: bool
    replay_status: Literal["not_evaluated"] | None = None
    provider_prompt_cache: Literal["not_reported"] = "not_reported"
    separate_response_cache: Literal[False] = False
    media_transcription_cache: Literal[False] = False


class PricingCacheResponse(ApiModel):
    kind: Literal["pricing"]
    key: str
    ttl: int
    value: JsonValue
    missing: bool
    invalid_entry: JsonValue
    run_id: str


class MongoInsertAction(ApiModel):
    kind: Literal["mongo.insert_one"]
    collection: str = Field(min_length=1, max_length=255)
    document: dict[str, JsonValue]


class MongoUpdateAction(ApiModel):
    kind: Literal["mongo.update_one"]
    collection: str = Field(min_length=1, max_length=255)
    document_id: JsonValue = Field(alias="_id")
    update: dict[Literal["$set", "$unset", "$inc"], dict[str, JsonValue]]


class MongoDeleteAction(ApiModel):
    kind: Literal["mongo.delete_one"]
    collection: str = Field(min_length=1, max_length=255)
    document_id: JsonValue = Field(alias="_id")


class RedisCommandAction(ApiModel):
    kind: Literal["redis.command"]
    command: Literal["SET", "DEL", "UNLINK", "HSET", "HDEL", "ZADD", "ZREM", "EXPIRE", "PERSIST"]
    args: list[JsonValue] = Field(min_length=1, max_length=100)

    @field_validator("command", mode="before")
    @classmethod
    def uppercase_command(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


class AiCacheClearAction(ApiModel):
    kind: Literal["ai_cache.clear"]
    cache_kind: Literal["messages", "tools", "context", "pricing"]
    chat_tid: StrictInt | None = None


ActionRequest = Annotated[
    MongoInsertAction | MongoUpdateAction | MongoDeleteAction | RedisCommandAction | AiCacheClearAction,
    Field(discriminator="kind"),
]


class ActionPrepareResponse(ApiModel):
    action_id: str
    run_id: str
    target: str
    operation: str
    preview: JsonValue
    warnings: list[str]
    expires_at: datetime
    confirmation_text: str


class ActionExecuteRequest(ApiModel):
    run_id: str = Field(min_length=1, max_length=128)
    confirmation_text: str = Field(min_length=1, max_length=512)


ActionStateName = Literal["prepared", "running", "succeeded", "failed", "unknown", "expired"]


class ActionStatusResponse(ApiModel):
    action_id: str
    run_id: str
    state: ActionStateName
    operation: str
    target: str
    expires_at: datetime
    result: JsonValue = None
    error: ApiErrorDetail | None = None
    status_url: str


class RestartResponse(ApiModel):
    status: Literal["reloading"]


@dataclass(slots=True)
class EventStore:
    max_events: int = MAX_EVENTS
    max_bytes: int = MAX_EVENT_BYTES
    _events: deque[StoredEvent] = field(default_factory=deque, init=False)
    _sizes: deque[int] = field(default_factory=deque, init=False)
    _bytes: int = field(default=0, init=False)
    _next_seq: int = field(default=1, init=False)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition, init=False)

    @property
    def oldest_seq(self) -> int | None:
        return self._events[0].seq if self._events else None

    @property
    def latest_seq(self) -> int | None:
        return self._events[-1].seq if self._events else None

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def event_bytes(self) -> int:
        return self._bytes

    async def append(self, event: CapturedEvent, *, session_id: str, run_id: str, pid: int) -> StoredEvent:
        async with self._condition:
            stored = StoredEvent(
                **event.model_dump(), seq=self._next_seq, session_id=session_id, run_id=run_id, pid=pid
            )
            self._next_seq += 1
            size = len(stored.model_dump_json().encode("utf-8"))
            self._events.append(stored)
            self._sizes.append(size)
            self._bytes += size
            while len(self._events) > self.max_events or self._bytes > self.max_bytes:
                self._events.popleft()
                self._bytes -= self._sizes.popleft()
            self._condition.notify_all()
            return stored

    async def query(
        self,
        *,
        after: int,
        before: int | None,
        limit: int,
        run_id: str | None,
        category: Category | None,
        trace_id: str | None,
        chat_tid: int | None,
        level: Level | None,
        origin: Origin | None,
        q: str | None,
        min_duration_ms: float | None,
        from_time: datetime | None,
        to_time: datetime | None,
    ) -> EventsResponse:
        async with self._condition:
            oldest_seq = self.oldest_seq
            latest_seq = self.latest_seq
            gap = oldest_seq is not None and after > 0 and after < oldest_seq - 1
            matched: list[EventSummary] = []
            literal_query = q.casefold() if q else None
            for event in self._events:
                if event.seq <= after or (before is not None and event.seq >= before):
                    continue
                if run_id is not None and event.run_id != run_id:
                    continue
                if category is not None and event.category != category:
                    continue
                if trace_id is not None and event.trace_id != trace_id:
                    continue
                if chat_tid is not None and event.chat_tid != chat_tid:
                    continue
                if level is not None and event.level != level:
                    continue
                if origin is not None and event.origin != origin:
                    continue
                if literal_query is not None and literal_query not in event.summary.casefold():
                    continue
                if min_duration_ms is not None and (event.duration_ms is None or event.duration_ms < min_duration_ms):
                    continue
                if from_time is not None and event.timestamp < from_time:
                    continue
                if to_time is not None and event.timestamp > to_time:
                    continue
                matched.append(EventSummary.from_event(event))
                if len(matched) >= limit:
                    break
            return EventsResponse(
                events=matched,
                next_after=matched[-1].seq if matched else after,
                oldest_seq=oldest_seq,
                latest_seq=latest_seq,
                gap=gap,
                dropped_total=0,
            )

    async def get(self, seq: int) -> StoredEvent | None:
        async with self._condition:
            if not self._events or seq < self._events[0].seq or seq > self._events[-1].seq:
                return None
            return next((event for event in self._events if event.seq == seq), None)

    async def wait_for_change(self, after: int, timeout: float) -> None:
        async with self._condition:
            if self.latest_seq is not None and self.latest_seq > after:
                return
            try:
                await asyncio.wait_for(self._condition.wait(), timeout)
            except TimeoutError:
                pass


type ControlDispatch = Callable[[str, str, str, int, dict[str, JsonValue]], Awaitable[ReplyFrame]]


class ControlCapacityError(RuntimeError):
    """The supervisor rejected work before writing a command frame."""


@dataclass(slots=True)
class PreparedActionRecord:
    action_id: str
    run_id: str
    channel_generation: int
    operation: str
    target: str
    payload: dict[str, JsonValue]
    preview: JsonValue
    warnings: list[str]
    confirmation_text: str
    expires_at: datetime
    state: ActionStateName = "prepared"
    result: JsonValue = None
    error: ApiErrorDetail | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    completion: asyncio.Future[None] | None = None
    dispatch_task: asyncio.Task[None] | None = None
    expiry_task: asyncio.Task[None] | None = None

    def response(self) -> ActionStatusResponse:
        return ActionStatusResponse(
            action_id=self.action_id,
            run_id=self.run_id,
            state=self.state,
            operation=self.operation,
            target=self.target,
            expires_at=self.expires_at,
            result=self.result,
            error=self.error,
            status_url=f"/api/v1/actions/{self.action_id}",
        )


@dataclass(slots=True)
class CollectorState:
    session_id: str
    bearer_token: str
    browser_credential: str
    csrf_token: str
    api_origin: str
    ui_origin: str
    sanitized_targets: dict[str, str | int]
    event_store: EventStore = field(default_factory=EventStore)
    run_id: str | None = None
    worker_pid: int | None = None
    state: Literal["starting", "ready", "reloading", "failed", "stopped"] = "starting"
    restart_required: bool = False
    dropped_total: int = 0
    recorder_errors: int = 0
    detail: str | None = None
    capabilities: dict[str, bool | str | int] = field(default_factory=dict)
    resources: dict[str, deque[dict[str, Any]]] = field(
        default_factory=lambda: {
            "worker": deque(maxlen=MAX_RESOURCE_SAMPLES),
            "collector": deque(maxlen=MAX_RESOURCE_SAMPLES),
            "vite": deque(maxlen=MAX_RESOURCE_SAMPLES),
        }
    )
    restart_worker: Callable[[], Awaitable[None]] | None = None
    control_dispatch: ControlDispatch | None = None
    channel_generation: int = 0
    known_secrets: tuple[str, ...] = ()
    _status_version: int = field(default=0, init=False)
    _status_condition: asyncio.Condition = field(default_factory=asyncio.Condition, init=False)
    _actions: OrderedDict[str, PreparedActionRecord] = field(default_factory=OrderedDict, init=False)
    _actions_guard: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def redaction_secrets(self) -> tuple[str, ...]:
        return (*self.known_secrets, self.bearer_token, self.browser_credential, self.csrf_token)

    def session_response(self) -> SessionResponse:
        normalized_detail, _detail_truncated, _detail_redacted = normalize_payload(self.detail, self.redaction_secrets)
        normalized_targets, _targets_truncated, _targets_redacted = normalize_payload(
            self.sanitized_targets, self.redaction_secrets
        )
        normalized_capabilities, _capabilities_truncated, _capabilities_redacted = normalize_payload(
            self.capabilities, self.redaction_secrets
        )
        return SessionResponse(
            session_id=self.session_id,
            run_id=self.run_id,
            state=self.state,
            restart_required=self.restart_required,
            worker_pid=self.worker_pid,
            sanitized_targets=normalized_targets if isinstance(normalized_targets, dict) else {},
            capabilities=normalized_capabilities if isinstance(normalized_capabilities, dict) else {},
            dropped_total=self.dropped_total,
            recorder_errors=self.recorder_errors,
            oldest_seq=self.event_store.oldest_seq,
            latest_seq=self.event_store.latest_seq,
            event_count=self.event_store.event_count,
            event_bytes=self.event_store.event_bytes,
            limits={
                "events": self.event_store.max_events,
                "event_bytes": self.event_store.max_bytes,
                "event_list_limit": 500,
                "query_items": MAX_QUERY_ITEMS,
                "query_result_bytes": 1024 * 1024,
                "resource_samples": MAX_RESOURCE_SAMPLES,
                "live_preparations": MAX_PREPARATIONS,
                "action_history": MAX_ACTION_HISTORY,
                "preparation_bytes": MAX_PREPARATION_BYTES,
                "preparation_ttl_seconds": PREPARATION_TTL_SECONDS,
            },
            csrf_token=self.csrf_token,
            detail=normalized_detail if isinstance(normalized_detail, str) else None,
        )

    async def notify_status_change(self) -> None:
        async with self._status_condition:
            self._status_version += 1
            self._status_condition.notify_all()

    async def wait_for_status_change(self, version: int, timeout: float) -> int:
        async with self._status_condition:
            if self._status_version != version:
                return self._status_version
            try:
                await asyncio.wait_for(self._status_condition.wait(), timeout)
            except TimeoutError:
                pass
            return self._status_version

    async def set_worker_channel(self, run_id: str, worker_pid: int | None) -> int:
        self.channel_generation += 1
        self.run_id = run_id
        self.worker_pid = worker_pid
        await self._expire_actions_for_channel_change()
        await self.notify_status_change()
        return self.channel_generation

    async def worker_channel_closed(self, run_id: str) -> None:
        if self.run_id != run_id:
            return
        self.channel_generation += 1
        await self._expire_actions_for_channel_change()
        await self.notify_status_change()

    async def _expire_actions_for_channel_change(self) -> None:
        now = datetime.now(UTC)
        async with self._actions_guard:
            for record in self._actions.values():
                if record.state == "prepared":
                    _mark_action_expired(record, now)
                elif record.state == "running":
                    record.state = "unknown"
                    record.error = ApiErrorDetail(
                        code="outcome_unknown",
                        message=_(
                            "Worker channel changed after dispatch; inspect the target before preparing another write"
                        ),
                        request_id=secrets.token_hex(8),
                        status_url=f"/api/v1/actions/{record.action_id}",
                    )
                    if record.completion is not None and not record.completion.done():
                        record.completion.set_result(None)

    async def audit(
        self,
        name: str,
        summary: str,
        payload: Any,
        *,
        outcome: Outcome | None = Outcome.OK,
        level: Level = Level.INFO,
    ) -> None:
        run_id = self.run_id or "no-worker"
        event = build_event(
            category=Category.PROCESS,
            name=name,
            phase=Phase.INSTANT,
            summary=summary,
            payload=payload,
            origin=Origin.DEBUGGER,
            outcome=outcome,
            level=level,
            known_secrets=self.redaction_secrets,
        )
        if event.trace_id is None:
            event = event.model_copy(update={"trace_id": secrets.token_hex(16), "span_id": secrets.token_hex(8)})
        await self.event_store.append(event, session_id=self.session_id, run_id=run_id, pid=os.getpid())


@dataclass(frozen=True, slots=True)
class AuthContext:
    bearer: bool


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "")
    state = getattr(request.app.state, "debug", None)
    secrets_to_hide = state.redaction_secrets if isinstance(state, CollectorState) else ()
    if REQUEST_ID_PATTERN.fullmatch(supplied) and not any(secret in supplied for secret in secrets_to_hide):
        return supplied
    return secrets.token_hex(8)


def _safe_error_code(value: str) -> str:
    return value if REQUEST_ID_PATTERN.fullmatch(value) else "worker_error"


def _api_error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    status_url: str | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ApiError(
            error=ApiErrorDetail(
                code=code,
                message=_(message),
                request_id=_request_id(request),
                status_url=status_url,
            )
        ).model_dump(),
    )


def _validate_collection(name: str) -> None:
    if not name or len(name) > 255 or "\x00" in name or name.startswith("system."):
        raise ValueError("collection must be a non-system collection in the configured database")


def _reject_unsafe_integers(value: Any) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int) and abs(value) > SAFE_INTEGER_MAX:
        raise ValueError("integers outside JavaScript's safe range require canonical Extended JSON")
    if isinstance(value, dict):
        for item in value.values():
            _reject_unsafe_integers(item)
    elif isinstance(value, list):
        for item in value:
            _reject_unsafe_integers(item)


def _validate_extended_json(value: Any) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        json_util.loads(encoded, json_options=json_util.CANONICAL_JSON_OPTIONS)
    except (TypeError, ValueError, KeyError, OverflowError, InvalidOperation, binascii.Error) as error:
        raise ValueError("invalid canonical Extended JSON") from error


def _reject_server_code(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in SERVER_CODE_OPERATORS:
                raise ValueError("server-side JavaScript operators are not allowed")
            _reject_server_code(item)
    elif isinstance(value, list):
        for item in value:
            _reject_server_code(item)


def _reject_placeholders(value: Any) -> None:
    if isinstance(value, str) and value in PLACEHOLDERS:
        raise ValueError("redaction and truncation placeholders cannot be written")
    if isinstance(value, dict):
        if "$truncated" in value:
            raise ValueError("truncation placeholders cannot be written")
        for item in value.values():
            _reject_placeholders(item)
    elif isinstance(value, list):
        for item in value:
            _reject_placeholders(item)


def _model_payload(model: BaseModel) -> dict[str, JsonValue]:
    payload = model.model_dump(mode="json", by_alias=True)
    assert isinstance(payload, dict)
    return payload


def _serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _normalized(value: Any, state: CollectorState) -> JsonValue:
    normalized, _truncated, _redacted = normalize_payload(value, state.redaction_secrets)
    return normalized


def _valid_expiry_argument(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 < value <= SAFE_INTEGER_MAX
    return isinstance(value, str) and value.isascii() and value.isdecimal() and 0 < int(value) <= SAFE_INTEGER_MAX


def _validate_redis_command(action: RedisCommandAction) -> None:
    args = action.args
    command = action.command
    _reject_unsafe_integers(args)
    _reject_placeholders(args)
    for argument in args:
        if isinstance(argument, bool) or not isinstance(argument, (str, int, float, dict)):
            raise ValueError("Redis arguments must be strings, finite numbers, or base64 objects")  # noqa: TRY004
        if isinstance(argument, float) and not math.isfinite(argument):
            raise ValueError("Redis arguments must be finite")
        if isinstance(argument, dict):
            Base64RedisBytes.model_validate(argument)
    if not args or not isinstance(args[0], (str, dict)):
        raise ValueError("the first Redis argument must be one RedisBytes key")
    fixed: dict[str, tuple[int, int | None]] = {
        "DEL": (1, 1),
        "UNLINK": (1, 1),
        "HDEL": (2, None),
        "ZREM": (2, None),
        "EXPIRE": (2, 2),
        "PERSIST": (1, 1),
    }
    if command in fixed:
        minimum, maximum = fixed[command]
        if len(args) < minimum or (maximum is not None and len(args) > maximum):
            raise ValueError(f"invalid {command} argument count")
        if command == "EXPIRE" and not _valid_expiry_argument(args[1]):
            raise ValueError("EXPIRE requires a positive integer TTL")
        return
    if command in {"HSET", "ZADD"}:
        if len(args) < 3 or len(args) % 2 == 0:
            raise ValueError(f"{command} requires one key and pairs")
        if command == "ZADD":
            for score in args[1::2]:
                if isinstance(score, bool) or not isinstance(score, (str, int, float)):
                    raise TypeError("ZADD scores must be finite numbers")
                try:
                    number = float(score)
                except ValueError:
                    number = math.nan
                if not math.isfinite(number):
                    raise ValueError("ZADD scores must be finite numbers")
        return
    if command == "SET":
        if len(args) < 2:
            raise ValueError("SET requires one key and one value")
        index = 2
        expiry_seen = False
        condition_seen = False
        while index < len(args):
            option = args[index]
            if not isinstance(option, str):
                raise ValueError("SET options must be text")  # noqa: TRY004
            upper = option.upper()
            if upper in {"EX", "PX"} and not expiry_seen and index + 1 < len(args):
                expiry = args[index + 1]
                valid_expiry = _valid_expiry_argument(expiry)
                if not valid_expiry:
                    raise ValueError("SET expiry must be a positive integer")
                expiry_seen = True
                index += 2
                continue
            if upper in {"NX", "XX"} and not condition_seen:
                condition_seen = True
                index += 1
                continue
            raise ValueError("SET supports only EX/PX and NX/XX")
        return
    raise ValueError("Redis command is not allowlisted")


def _validate_action(action: ActionRequest) -> tuple[dict[str, JsonValue], str, list[str]]:
    payload = _model_payload(action)
    _reject_unsafe_integers(payload)
    _reject_placeholders(payload)
    warnings = [_("Preview does not lock the target; inspect it again if execution outcome is unknown.")]
    if isinstance(action, MongoInsertAction):
        _validate_collection(action.collection)
        if not action.document:
            raise ValueError("insert document must not be empty")
        _reject_server_code(action.document)
        _validate_extended_json(action.document)
        if "_id" not in action.document:
            document = dict(action.document)
            document["_id"] = {"$oid": str(ObjectId())}
            payload["document"] = document
        return payload, _("Mongo collection {collection}").format(collection=action.collection), warnings
    if isinstance(action, MongoUpdateAction):
        _validate_collection(action.collection)
        if action.document_id is None or not action.update or any(not fields for fields in action.update.values()):
            raise ValueError("update requires exact _id and non-empty operator fields")
        if any(field == "_id" or field.startswith("_id.") for fields in action.update.values() for field in fields):
            raise ValueError("_id cannot be edited")
        _reject_server_code(action.update)
        _validate_extended_json(action.document_id)
        _validate_extended_json(action.update)
        return payload, _("Mongo document in {collection}").format(collection=action.collection), warnings
    if isinstance(action, MongoDeleteAction):
        _validate_collection(action.collection)
        if action.document_id is None:
            raise ValueError("delete requires exact _id")
        _validate_extended_json(action.document_id)
        return payload, _("Mongo document in {collection}").format(collection=action.collection), warnings
    if isinstance(action, RedisCommandAction):
        _validate_redis_command(action)
        return payload, _("Redis key for {command}").format(command=action.command), warnings
    if action.cache_kind == "pricing":
        if action.chat_tid is not None:
            raise ValueError("pricing is global and forbids chat_tid")
        return payload, _("Global AI pricing cache"), [*warnings, _("Pricing cache is global.")]
    if action.chat_tid is None or isinstance(action.chat_tid, bool) or abs(action.chat_tid) > SAFE_INTEGER_MAX:
        raise ValueError("chat_tid is required and must be a safe Telegram integer")
    return (
        payload,
        _("AI {kind} cache for chat {chat_tid}").format(kind=action.cache_kind, chat_tid=action.chat_tid),
        warnings,
    )


async def authenticate_request(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    browser_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> AuthContext:
    state: CollectorState = request.app.state.debug
    if authorization is not None:
        scheme, _, credential = authorization.partition(" ")
        if scheme == "Bearer" and hmac.compare_digest(credential, state.bearer_token):
            return AuthContext(bearer=True)
    if browser_cookie is not None and hmac.compare_digest(browser_cookie, state.browser_credential):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf = request.headers.get("x-debug-csrf", "")
            if not hmac.compare_digest(csrf, state.csrf_token):
                raise _api_error(request, status.HTTP_403_FORBIDDEN, "csrf_failed", "Invalid CSRF token")
        return AuthContext(bearer=False)
    raise _api_error(request, status.HTTP_401_UNAUTHORIZED, "unauthorized", "Authentication required")


async def _dispatch_read(
    request: Request,
    operation: str,
    payload: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue], str]:
    try:
        payload_size = _serialized_size(payload)
    except (TypeError, ValueError) as error:
        raise _api_error(request, 422, "invalid_json", "Request must contain strict finite JSON") from error
    if payload_size > MAX_PREPARATION_BYTES:
        raise _api_error(request, 413, "request_too_large", "Inspector request exceeds 64 KiB")
    state: CollectorState = request.app.state.debug
    if state.state != "ready" or state.run_id is None or state.control_dispatch is None:
        raise _api_error(request, 503, "worker_offline", "Inspector queries require a ready worker")
    run_id = state.run_id
    generation = state.channel_generation
    request_id = uuid.uuid4().hex
    dispatch = state.control_dispatch
    try:
        async with asyncio.timeout(READ_DEADLINE_SECONDS):
            reply = await dispatch(request_id, run_id, operation, generation, payload)
    except ControlCapacityError as error:
        raise _api_error(request, 429, "control_capacity", "Worker control channel is at capacity") from error
    except TimeoutError as error:
        raise _api_error(request, 504, "read_timeout", "Inspector operation exceeded the 5 second limit") from error
    except (ConnectionError, EOFError, OSError) as error:
        raise _api_error(request, 503, "worker_offline", "Worker control channel is unavailable") from error
    if reply.run_id != run_id or reply.request_id != request_id:
        raise _api_error(request, 503, "stale_worker_reply", "Worker reply did not match the active request")
    if reply.error is not None:
        code = _safe_error_code(reply.error.code)
        message_value = _normalized(reply.error.message, state)
        message = message_value if isinstance(message_value, str) else _("Inspector operation failed")
        error_status = 504 if code == "read_timeout" else 422
        raise _api_error(request, error_status, code, message)
    result = _normalized(reply.result, state)
    if not isinstance(result, dict):
        raise _api_error(request, 503, "invalid_worker_reply", "Worker returned an invalid inspector response")
    return result, run_id


async def _prepare_preview(request: Request, action: ActionRequest, payload: dict[str, JsonValue]) -> JsonValue:
    if isinstance(action, (MongoUpdateAction, MongoDeleteAction)):
        result, _run_id = await _dispatch_read(
            request,
            "mongo.query",
            {
                "collection": action.collection,
                "filter": {"_id": payload["_id"]},
                "projection": None,
                "sort": [],
                "skip": 0,
                "limit": 1,
            },
        )
        items = result.get("items", [])
        return {"current_document": items[0] if isinstance(items, list) and items else None, "action": payload}
    return {"action": payload}


def _mark_action_expired(record: PreparedActionRecord, now: datetime) -> None:
    record.state = "expired"
    record.expires_at = min(record.expires_at, now)
    record.payload = {}
    if (
        record.expiry_task is not None
        and record.expiry_task is not asyncio.current_task()
        and not record.expiry_task.done()
    ):
        record.expiry_task.cancel()


async def _expire_prepared_action(record: PreparedActionRecord) -> None:
    delay = max(0.0, (record.expires_at - datetime.now(UTC)).total_seconds())
    await asyncio.sleep(delay)
    async with record.lock:
        if record.state == "prepared":
            _mark_action_expired(record, datetime.now(UTC))


async def _expire_and_prune_actions(state: CollectorState, *, reserve: int = 0) -> None:
    now = datetime.now(UTC)
    async with state._actions_guard:
        for record in state._actions.values():
            if record.state == "prepared" and record.expires_at <= now:
                _mark_action_expired(record, now)
        while len(state._actions) > MAX_ACTION_HISTORY - reserve:
            removable = next(
                (
                    key
                    for key, record in state._actions.items()
                    if record.state != "running" and (record.dispatch_task is None or record.dispatch_task.done())
                ),
                None,
            )
            if removable is None:
                break
            removed = state._actions.pop(removable)
            if removed.expiry_task is not None and not removed.expiry_task.done():
                removed.expiry_task.cancel()


async def _complete_action_dispatch(
    state: CollectorState,
    record: PreparedActionRecord,
    request_id: str,
    dispatch: ControlDispatch,
) -> None:
    payload = record.payload
    record.payload = {}
    try:
        reply = await dispatch(
            request_id,
            record.run_id,
            record.operation,
            record.channel_generation,
            payload,
        )
    except ControlCapacityError:
        async with record.lock:
            record.state = "failed"
            record.error = ApiErrorDetail(
                code="control_capacity",
                message=_("Worker control channel was at capacity; the action was not dispatched"),
                request_id=request_id,
            )
            if record.completion is not None and not record.completion.done():
                record.completion.set_result(None)
        await state.audit(
            "debugger.action.result",
            _("Action {action_id} was not dispatched").format(action_id=record.action_id),
            {"action_id": record.action_id, "operation": record.operation, "state": record.state},
            outcome=Outcome.ERROR,
            level=Level.WARNING,
        )
        return
    # This is the isolation boundary around a supervisor-owned IPC callback. Once invoked,
    # any failure can leave a write applied without a reply, so every exception is unknown.
    except Exception as error:  # noqa: BLE001
        async with record.lock:
            if record.state == "running":
                record.state = "unknown"
                record.error = ApiErrorDetail(
                    code="outcome_unknown",
                    message=_(
                        "Worker channel closed after dispatch; inspect the target before preparing another write"
                    ),
                    request_id=request_id,
                    status_url=f"/api/v1/actions/{record.action_id}",
                )
            if record.completion is not None and not record.completion.done():
                record.completion.set_result(None)
        await state.audit(
            "debugger.action.unknown",
            _("Action {action_id} outcome is unknown").format(action_id=record.action_id),
            {"action_id": record.action_id, "operation": record.operation, "error_type": type(error).__name__},
            outcome=Outcome.ERROR,
            level=Level.WARNING,
        )
        return
    async with record.lock:
        if reply.run_id != record.run_id or reply.request_id != request_id:
            record.state = "unknown"
            record.error = ApiErrorDetail(
                code="stale_worker_reply",
                message=_("Worker reply did not match the dispatched action"),
                request_id=request_id,
                status_url=f"/api/v1/actions/{record.action_id}",
            )
        elif reply.error is not None and reply.error.ambiguous:
            record.state = "unknown"
            message = _normalized(reply.error.message, state)
            record.error = ApiErrorDetail(
                code=_safe_error_code(reply.error.code),
                message=message if isinstance(message, str) else _("Operation outcome is unknown"),
                request_id=request_id,
                status_url=f"/api/v1/actions/{record.action_id}",
            )
        elif reply.error is not None:
            record.state = "failed"
            message = _normalized(reply.error.message, state)
            record.error = ApiErrorDetail(
                code=_safe_error_code(reply.error.code),
                message=message if isinstance(message, str) else _("Operation failed"),
                request_id=request_id,
            )
        else:
            record.state = "succeeded"
            record.result = _normalized(reply.result, state)
            record.error = None
        if record.completion is not None and not record.completion.done():
            record.completion.set_result(None)
    await state.audit(
        "debugger.action.result",
        _("Action {action_id} finished as {state}").format(action_id=record.action_id, state=record.state),
        {
            "action_id": record.action_id,
            "operation": record.operation,
            "state": record.state,
            "result": record.result,
            "error": record.error,
        },
        outcome=Outcome.OK if record.state == "succeeded" else Outcome.ERROR,
        level=Level.INFO if record.state == "succeeded" else Level.WARNING,
    )


def create_app(state: CollectorState) -> FastAPI:
    app = FastAPI(
        title=_("Sophie Development Debugger"),
        version="1.0.0",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        openapi_tags=[
            {"name": "session"},
            {"name": "events"},
            {"name": "resources"},
            {"name": "mongo"},
            {"name": "redis"},
            {"name": "ai-cache"},
            {"name": "actions"},
            {"name": "worker"},
        ],
    )
    app.state.debug = state

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, error: HTTPException) -> JSONResponse:
        if isinstance(error.detail, dict) and "error" in error.detail:
            return JSONResponse(status_code=error.status_code, content=error.detail)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": "http_error",
                    "message": _("Request failed"),
                    "request_id": _request_id(_request),
                    "status_url": None,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_input",
                    "message": _("Request validation failed"),
                    "request_id": _request_id(_request),
                    "status_url": None,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": _("Internal debugger error"),
                    "request_id": _request_id(_request),
                    "status_url": None,
                }
            },
        )

    @app.middleware("http")
    async def validate_local_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        host = request.headers.get("host", "")
        allowed_hosts = {state.api_origin.removeprefix("http://"), state.ui_origin.removeprefix("http://")}
        if host not in allowed_hosts:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "invalid_host",
                        "message": _("Unexpected Host header"),
                        "request_id": _request_id(request),
                        "status_url": None,
                    }
                },
            )
        origin = request.headers.get("origin")
        if origin is not None and origin != state.ui_origin:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "invalid_origin",
                        "message": _("Unexpected Origin header"),
                        "request_id": _request_id(request),
                        "status_url": None,
                    }
                },
            )
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if len(body) > MAX_HTTP_JSON_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "request_too_large",
                            "message": _("JSON request exceeds 2 MiB"),
                            "request_id": _request_id(request),
                            "status_url": None,
                        }
                    },
                )
            if body:
                try:
                    decoded_body = strict_json_loads(body)
                    _reject_unsafe_integers(decoded_body)
                except (FrameError, ValueError):
                    return JSONResponse(
                        status_code=422,
                        content={
                            "error": {
                                "code": "invalid_json",
                                "message": _("Request must contain strict finite JSON and safe bare integers"),
                                "request_id": _request_id(request),
                                "status_url": None,
                            }
                        },
                    )
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/v1/session/auth", status_code=204, tags=["session"])
    async def authenticate_browser(
        request: Request, response: Response, authorization: Annotated[str | None, Header()] = None
    ) -> None:
        if await request.body():
            raise _api_error(request, 422, "invalid_body", "Authentication request body must be empty")
        if request.headers.get("origin") != state.ui_origin:
            raise _api_error(request, 403, "invalid_origin", "Exact UI Origin is required")
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme != "Bearer" or not hmac.compare_digest(credential, state.bearer_token):
            raise _api_error(request, 401, "unauthorized", "Invalid session token")
        response.set_cookie(SESSION_COOKIE, state.browser_credential, httponly=True, samesite="strict", path="/api/v1")

    @app.get("/api/v1/session", response_model=SessionResponse, tags=["session"])
    async def get_session(_auth: Annotated[AuthContext, Depends(authenticate_request)]) -> SessionResponse:
        return state.session_response()

    @app.get("/api/v1/events", response_model=EventsResponse, tags=["events"])
    async def get_events(
        request: Request,
        _auth: Annotated[AuthContext, Depends(authenticate_request)],
        after: Annotated[int, Query(ge=0, le=SAFE_INTEGER_MAX)] = 0,
        before: Annotated[int | None, Query(gt=0, le=SAFE_INTEGER_MAX)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        run_id: str | None = None,
        category: Category | None = None,
        trace_id: str | None = None,
        chat_tid: Annotated[int | None, Query(ge=-SAFE_INTEGER_MAX, le=SAFE_INTEGER_MAX)] = None,
        level: Level | None = None,
        origin: Origin | None = None,
        q: Annotated[str | None, Query(max_length=256)] = None,
        min_duration_ms: Annotated[float | None, Query(ge=0)] = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> EventsResponse:
        if min_duration_ms is not None and not math.isfinite(min_duration_ms):
            raise _api_error(request, 422, "invalid_duration", "min_duration_ms must be finite")
        for timestamp_value in (from_time, to_time):
            if timestamp_value is not None and timestamp_value.utcoffset() != timedelta(0):
                raise _api_error(request, 422, "invalid_time", "Event time filters must use UTC RFC3339 timestamps")
        if from_time is not None and to_time is not None and from_time > to_time:
            raise _api_error(request, 422, "invalid_time_range", "from_time must not be later than to_time")
        response = await state.event_store.query(
            after=after,
            before=before,
            limit=limit,
            run_id=run_id,
            category=category,
            trace_id=trace_id,
            chat_tid=chat_tid,
            level=level,
            origin=origin,
            q=q,
            min_duration_ms=min_duration_ms,
            from_time=from_time,
            to_time=to_time,
        )
        response.dropped_total = state.dropped_total
        return response

    @app.get("/api/v1/events/stream", tags=["events"])
    async def stream_events(
        request: Request,
        _auth: Annotated[AuthContext, Depends(authenticate_request)],
        after: Annotated[int, Query(ge=0, le=SAFE_INTEGER_MAX)] = 0,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        cursor = after
        if last_event_id is not None:
            try:
                parsed_event_id = int(last_event_id)
                if not 0 <= parsed_event_id <= SAFE_INTEGER_MAX:
                    raise ValueError
                cursor = max(cursor, parsed_event_id)
            except ValueError as error:
                raise _api_error(
                    request, 422, "invalid_last_event_id", "Last-Event-ID must be a safe non-negative integer"
                ) from error

        async def generate() -> AsyncIterator[str]:
            current = cursor
            status_version = state._status_version
            yield f"event: status\ndata: {state.session_response().model_dump_json()}\n\n"
            while not await request.is_disconnected():
                if status_version != state._status_version:
                    status_version = state._status_version
                    yield f"event: status\ndata: {state.session_response().model_dump_json()}\n\n"
                page = await state.event_store.query(
                    after=current,
                    before=None,
                    limit=500,
                    run_id=None,
                    category=None,
                    trace_id=None,
                    chat_tid=None,
                    level=None,
                    origin=None,
                    q=None,
                    min_duration_ms=None,
                    from_time=None,
                    to_time=None,
                )
                if page.gap:
                    yield f"event: gap\ndata: {json.dumps({'oldest_seq': page.oldest_seq, 'latest_seq': page.latest_seq}, separators=(',', ':'))}\n\n"
                    current = (page.oldest_seq or 1) - 1
                    continue
                if page.events:
                    for event in page.events:
                        yield f"id: {event.seq}\nevent: event\ndata: {event.model_dump_json()}\n\n"
                        current = event.seq
                    continue
                event_wait = asyncio.create_task(state.event_store.wait_for_change(current, 15))
                status_wait = asyncio.create_task(state.wait_for_status_change(status_version, 15))
                _done, pending = await asyncio.wait({event_wait, status_wait}, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                latest_seq = state.event_store.latest_seq
                if (latest_seq is None or latest_seq <= current) and state._status_version == status_version:
                    yield ": heartbeat\n\n"

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )

    @app.get("/api/v1/events/{seq}", response_model=StoredEvent, tags=["events"])
    async def get_event(
        request: Request, seq: int, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> StoredEvent:
        event = await state.event_store.get(seq)
        if event is None:
            raise _api_error(request, 404, "event_evicted", "Event is not retained")
        return event

    @app.get("/api/v1/resources", response_model=ResourceResponse, tags=["resources"])
    async def get_resources(
        _auth: Annotated[AuthContext, Depends(authenticate_request)],
        run_id: str | None = None,
    ) -> ResourceResponse:
        selected_run = run_id or state.run_id
        worker_samples = [
            sample
            for sample in state.resources["worker"]
            if selected_run is None or sample.get("run_id", selected_run) == selected_run
        ]
        return ResourceResponse(
            run_id=selected_run,
            worker=worker_samples,
            collector=list(state.resources["collector"]),
            vite=list(state.resources["vite"]),
        )

    @app.get("/api/v1/mongo/collections", response_model=MongoCollectionsResponse, tags=["mongo"])
    async def mongo_collections(
        request: Request, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> MongoCollectionsResponse:
        result, run_id = await _dispatch_read(request, "mongo.collections", {})
        return MongoCollectionsResponse.model_validate({**result, "run_id": run_id})

    @app.post("/api/v1/mongo/query", response_model=MongoQueryResponse, tags=["mongo"])
    async def mongo_query(
        request: Request, body: MongoQueryRequest, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> MongoQueryResponse:
        result, run_id = await _dispatch_read(request, "mongo.query", _model_payload(body))
        return MongoQueryResponse.model_validate({**result, "run_id": run_id})

    @app.post("/api/v1/redis/query", response_model=RedisQueryResponse, tags=["redis"])
    async def redis_query(
        request: Request, body: RedisQueryRequest, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> RedisQueryResponse:
        result, run_id = await _dispatch_read(request, "redis.query", _model_payload(body))
        return RedisQueryResponse.model_validate({**result, "run_id": run_id})

    @app.get("/api/v1/ai-cache/pricing", response_model=PricingCacheResponse, tags=["ai-cache"])
    async def ai_pricing(
        request: Request, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> PricingCacheResponse:
        result, run_id = await _dispatch_read(request, "ai_cache.pricing", {})
        return PricingCacheResponse.model_validate({**result, "run_id": run_id})

    @app.get("/api/v1/ai-cache/{chat_tid}", response_model=AiCacheResponse, tags=["ai-cache"])
    async def ai_cache(
        request: Request,
        chat_tid: int,
        _auth: Annotated[AuthContext, Depends(authenticate_request)],
        kind: Literal["messages", "tools"] = "messages",
        offset: Annotated[int, Query(ge=0, le=SAFE_INTEGER_MAX)] = 0,
        cursor: Annotated[int, Query(ge=0, le=SAFE_INTEGER_MAX)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_QUERY_ITEMS)] = 50,
    ) -> AiCacheResponse:
        if abs(chat_tid) > SAFE_INTEGER_MAX:
            raise _api_error(request, 422, "invalid_chat_tid", "chat_tid must be a safe Telegram integer")
        operation = f"ai_cache.{kind}"
        payload: dict[str, JsonValue] = (
            {"chat_tid": chat_tid, "limit": limit, "offset": offset}
            if kind == "messages"
            else {"chat_tid": chat_tid, "limit": limit, "cursor": cursor}
        )
        result, run_id = await _dispatch_read(request, operation, payload)
        return AiCacheResponse.model_validate({**result, "run_id": run_id})

    @app.post("/api/v1/actions/prepare", response_model=ActionPrepareResponse, tags=["actions"])
    async def prepare_action(
        request: Request, body: ActionRequest, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> ActionPrepareResponse:
        if state.state != "ready" or state.run_id is None or state.control_dispatch is None:
            raise _api_error(request, 503, "worker_offline", "Actions require a ready worker")
        prepared_run_id = state.run_id
        prepared_generation = state.channel_generation
        try:
            payload, target, warnings = _validate_action(body)
            if _serialized_size(payload) > MAX_PREPARATION_BYTES:
                raise _api_error(request, 413, "action_too_large", "Prepared action exceeds 64 KiB")
            preview = await _prepare_preview(request, body, payload)
        except HTTPException:
            raise
        except (TypeError, ValueError) as error:
            raise _api_error(request, 422, "invalid_action", str(error)) from error
        await _expire_and_prune_actions(state, reserve=1)
        async with state._actions_guard:
            if (
                state.state != "ready"
                or state.run_id != prepared_run_id
                or state.channel_generation != prepared_generation
            ):
                raise _api_error(
                    request, 409, "worker_changed", "Worker changed while the action preview was being prepared"
                )
            while len(state._actions) >= MAX_ACTION_HISTORY:
                removable = next(
                    (
                        key
                        for key, existing in state._actions.items()
                        if existing.state != "running"
                        and (existing.dispatch_task is None or existing.dispatch_task.done())
                    ),
                    None,
                )
                if removable is None:
                    raise _api_error(request, 429, "action_history_capacity", "Action status history is at capacity")
                removed = state._actions.pop(removable)
                if removed.expiry_task is not None and not removed.expiry_task.done():
                    removed.expiry_task.cancel()
            live = sum(record.state in {"prepared", "running"} for record in state._actions.values())
            if live >= MAX_PREPARATIONS:
                raise _api_error(request, 429, "action_capacity", "Too many live prepared actions")
            action_id = secrets.token_urlsafe(24)
            expires_at = datetime.now(UTC) + timedelta(seconds=PREPARATION_TTL_SECONDS)
            confirmation_text = f"EXECUTE {body.kind} {action_id}"
            record = PreparedActionRecord(
                action_id=action_id,
                run_id=prepared_run_id,
                channel_generation=prepared_generation,
                operation=body.kind,
                target=target,
                payload=payload,
                preview=_normalized(preview, state),
                warnings=warnings,
                confirmation_text=confirmation_text,
                expires_at=expires_at,
            )
            if _serialized_size({"payload": record.payload, "preview": record.preview}) > MAX_PREPARATION_BYTES:
                raise _api_error(request, 413, "action_too_large", "Prepared action and preview exceed 64 KiB")
            state._actions[action_id] = record
            record.expiry_task = asyncio.create_task(
                _expire_prepared_action(record),
                name=f"debug-action-expiry-{action_id}",
            )
        await state.audit(
            "debugger.action.prepare",
            _("Prepared {operation}").format(operation=body.kind),
            {"action_id": action_id, "operation": body.kind, "target": target, "preview": record.preview},
        )
        return ActionPrepareResponse(
            action_id=action_id,
            run_id=record.run_id,
            target=target,
            operation=record.operation,
            preview=record.preview,
            warnings=warnings,
            expires_at=expires_at,
            confirmation_text=confirmation_text,
        )

    @app.post(
        "/api/v1/actions/{action_id}/execute",
        response_model=ActionStatusResponse,
        responses={202: {"model": ActionStatusResponse}, 504: {"model": ApiError}},
        tags=["actions"],
    )
    async def execute_action(
        request: Request,
        action_id: str,
        body: ActionExecuteRequest,
        _auth: Annotated[AuthContext, Depends(authenticate_request)],
    ) -> Response | ActionStatusResponse:
        await _expire_and_prune_actions(state)
        record = state._actions.get(action_id)
        if record is None:
            raise _api_error(request, 404, "unknown_action", "Prepared action is not retained")
        async with record.lock:
            if record.state == "prepared" and record.expires_at <= datetime.now(UTC):
                _mark_action_expired(record, datetime.now(UTC))
            if record.state == "expired":
                raise _api_error(request, 409, "expired_action", "Prepared action has expired")
            if (
                body.run_id != record.run_id
                or state.run_id != record.run_id
                or state.channel_generation != record.channel_generation
            ):
                raise _api_error(request, 409, "wrong_run", "Prepared action belongs to a different worker run")
            if not hmac.compare_digest(body.confirmation_text, record.confirmation_text):
                raise _api_error(
                    request, 403, "confirmation_failed", "Confirmation text does not match the prepared action"
                )
            if record.state in TERMINAL_ACTION_STATES or record.state == "unknown":
                return record.response()
            if record.state == "running":
                return JSONResponse(status_code=202, content=record.response().model_dump(mode="json"))
            if state.state != "ready" or state.control_dispatch is None:
                raise _api_error(request, 503, "worker_offline", "Actions require a ready worker")
            record.state = "running"
            if record.expiry_task is not None and not record.expiry_task.done():
                record.expiry_task.cancel()
            loop = asyncio.get_running_loop()
            record.completion = loop.create_future()
            reply_deadline = loop.time() + WRITE_REPLY_DEADLINE_SECONDS
            request_id = uuid.uuid4().hex
            dispatch = state.control_dispatch
            assert dispatch is not None
            record.dispatch_task = asyncio.create_task(
                _complete_action_dispatch(state, record, request_id, dispatch),
                name=f"debug-action-{action_id}",
            )
            completion = record.completion
        await state.audit(
            "debugger.action.execute",
            _("Executing {operation}").format(operation=record.operation),
            {"action_id": action_id, "operation": record.operation, "target": record.target},
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(completion),
                max(0.0, reply_deadline - asyncio.get_running_loop().time()),
            )
        except TimeoutError:
            timed_out = False
            async with record.lock:
                if record.state == "running":
                    timed_out = True
                    record.state = "unknown"
                    record.error = ApiErrorDetail(
                        code="outcome_unknown",
                        message=_("No definitive reply arrived; inspect the target before preparing another write"),
                        request_id=request_id,
                        status_url=f"/api/v1/actions/{action_id}",
                    )
            if timed_out:
                raise _api_error(
                    request,
                    504,
                    "outcome_unknown",
                    "No definitive write reply arrived; inspect action status and target data",
                    status_url=f"/api/v1/actions/{action_id}",
                )
        if record.error is not None and record.error.code == "control_capacity":
            raise _api_error(request, 429, "control_capacity", record.error.message)
        if record.state == "unknown":
            raise _api_error(
                request,
                504,
                "outcome_unknown",
                "Write outcome is unknown; inspect action status and target data",
                status_url=f"/api/v1/actions/{action_id}",
            )
        return record.response()

    @app.get("/api/v1/actions/{action_id}", response_model=ActionStatusResponse, tags=["actions"])
    async def get_action(
        request: Request, action_id: str, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> ActionStatusResponse:
        await _expire_and_prune_actions(state)
        record = state._actions.get(action_id)
        if record is None:
            raise _api_error(request, 404, "unknown_action", "Prepared action is not retained")
        return record.response()

    @app.post("/api/v1/worker/restart", status_code=202, response_model=RestartResponse, tags=["worker"])
    async def restart_worker(
        request: Request, _auth: Annotated[AuthContext, Depends(authenticate_request)]
    ) -> RestartResponse:
        if await request.body():
            raise _api_error(request, 422, "invalid_body", "Worker restart request body must be empty")
        if state.restart_worker is None:
            raise _api_error(request, 503, "worker_unavailable", "Worker restart is unavailable")
        await state.restart_worker()
        return RestartResponse(status="reloading")

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    async def debugger_openapi(_auth: Annotated[AuthContext, Depends(authenticate_request)]) -> dict[str, Any]:
        return app.openapi()

    return app
