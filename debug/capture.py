from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import io
import itertools
import math
import queue
import re
import socket
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TextIO
from urllib.parse import unquote

from bson import DBRef, Decimal128, Int64, ObjectId, json_util
from pydantic import BaseModel

from debug.protocol import (
    TELEMETRY_FRAME_LIMIT,
    CapturedEvent,
    Category,
    EventFrame,
    FrameError,
    Level,
    Origin,
    Outcome,
    Phase,
    encode_frame,
    strict_json_loads,
)

MAX_CAPTURE_DEPTH = 12
MAX_CAPTURE_ENTRIES = 1_000
MAX_TELEMETRY_RECORDS = 2_048
MAX_TELEMETRY_BYTES = 8 * 1024 * 1024
MAX_TEXT_LENGTH = 32 * 1024
MAX_BINARY_BYTES = 24 * 1024
REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
SECRET_TERMS = {"token", "password", "authorization", "cookie", "secret"}
TOKEN_COUNTER_TERMS = {"usage", "count", "prompt", "completion", "input", "output"}
URI_USERINFO_PATTERN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]+@)", re.IGNORECASE)
BOT_TOKEN_URL_PATTERN = re.compile(r"(?P<prefix>/bot)\d+:[A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    dispatch_task_id: str | None
    update_id: int | None
    chat_tid: int | None
    origin: Origin


TRACE_CONTEXT: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar("debug_trace_context", default=None)
_TASK_IDS: weakref.WeakKeyDictionary[asyncio.Task[Any], str] = weakref.WeakKeyDictionary()
_TASK_COUNTER = itertools.count(1)

_RUNTIME_READY = threading.Event()


def task_id() -> str | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    if task is None:
        return None
    identifier = _TASK_IDS.get(task)
    if identifier is None:
        identifier = f"task-{next(_TASK_COUNTER)}"
        _TASK_IDS[task] = identifier
    return identifier


def set_runtime_ready(ready: bool) -> None:
    if ready:
        _RUNTIME_READY.set()
    else:
        _RUNTIME_READY.clear()


def new_trace_context(
    *,
    update_id: int | None = None,
    chat_tid: int | None = None,
    origin: Origin = Origin.UPDATE,
) -> TraceContext:
    return TraceContext(
        trace_id=uuid.uuid4().hex,
        span_id=uuid.uuid4().hex[:16],
        dispatch_task_id=task_id(),
        update_id=update_id,
        chat_tid=chat_tid,
        origin=origin,
    )


def child_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _is_secret_key(key: str, value: Any) -> bool:
    separated = CAMEL_CASE_BOUNDARY.sub("_", key)
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", separated.casefold()) if part)
    has_counter_term = bool(TOKEN_COUNTER_TERMS.intersection(parts))
    if has_counter_term and isinstance(value, (Mapping, int, float, Decimal, Int64)):
        return False
    if SECRET_TERMS.intersection(parts):
        return True
    pairs = set(itertools.pairwise(parts))
    if {("api", "key"), ("bot", "token"), ("private", "key")} & pairs:
        return True
    collapsed = "".join(parts)
    secret_forms = ("token", "password", "authorization", "cookie", "secret", "apikey", "privatekey")
    return collapsed.startswith(secret_forms) or collapsed.endswith(secret_forms)


def known_secret_values(values: Mapping[str, str]) -> tuple[str, ...]:
    secrets_found: set[str] = set()
    for key, value in values.items():
        if value and _is_secret_key(key, value):
            secrets_found.add(value)
        for match in URI_USERINFO_PATTERN.finditer(value):
            userinfo = match.group("userinfo").removesuffix("@")
            secrets_found.add(userinfo)
            secrets_found.update(part for part in userinfo.split(":", 1) if part)
            decoded = unquote(userinfo)
            secrets_found.add(decoded)
            secrets_found.update(part for part in decoded.split(":", 1) if part)
    return tuple(sorted(secrets_found, key=len, reverse=True))


class PayloadNormalizer:
    def __init__(self, known_secrets: Sequence[str] = ()) -> None:
        self._known_secrets = tuple(secret for secret in known_secrets if secret)
        self._visited = 0
        self.truncated = False
        self.redacted = False

    def normalize(self, value: Any) -> Any:
        return self._visit(value, depth=0, key=None)

    def _visit(self, value: Any, *, depth: int, key: str | None) -> Any:
        if key is not None and _is_secret_key(key, value):
            self.redacted = True
            return REDACTED
        if depth > MAX_CAPTURE_DEPTH or self._visited >= MAX_CAPTURE_ENTRIES:
            self.truncated = True
            return TRUNCATED
        self._visited += 1

        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str):
            return self._normalize_text(value)
        if isinstance(value, Int64):
            return {"$numberLong": str(value)}
        if isinstance(value, int):
            if abs(value) > 2**53 - 1:
                return {"$numberLong": str(value)}
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                if math.isnan(value):
                    encoded = "NaN"
                elif value > 0:
                    encoded = "Infinity"
                else:
                    encoded = "-Infinity"
                return {"$numberDouble": encoded}
            return value
        if isinstance(value, (ObjectId, DBRef, Decimal128, datetime)):
            encoded = json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS)
            return strict_json_loads(encoded)
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return {"$numberDecimal": str(value)}
        if isinstance(value, (bytes, bytearray, memoryview)):
            binary = bytes(value[:MAX_BINARY_BYTES])
            encoded_binary: dict[str, Any] = {"base64": base64.b64encode(binary).decode("ascii")}
            if len(value) > MAX_BINARY_BYTES:
                self.truncated = True
                encoded_binary["truncated"] = True
                encoded_binary["original_bytes"] = len(value)
            return encoded_binary
        if isinstance(value, BaseModel):
            return self._visit(value.model_dump(mode="python"), depth=depth + 1, key=key)
        if isinstance(value, Mapping):
            normalized: dict[str, Any] = {}
            for item_key, item_value in value.items():
                if self._visited >= MAX_CAPTURE_ENTRIES:
                    self.truncated = True
                    normalized[TRUNCATED] = True
                    break
                normalized_key = item_key if isinstance(item_key, str) else f"<{type(item_key).__name__}>"
                normalized[normalized_key] = self._visit(item_value, depth=depth + 1, key=normalized_key)
            return normalized
        if isinstance(value, (list, tuple, set, frozenset)):
            normalized_items: list[Any] = []
            for item in value:
                if self._visited >= MAX_CAPTURE_ENTRIES:
                    self.truncated = True
                    normalized_items.append(TRUNCATED)
                    break
                normalized_items.append(self._visit(item, depth=depth + 1, key=None))
            return normalized_items
        if isinstance(value, BaseException):
            return {
                "type": type(value).__name__,
                "message": self._normalize_text(str(value)),
            }
        if isinstance(value, (io.IOBase, TextIO)):
            return {"$type": f"{type(value).__module__}.{type(value).__qualname__}"}
        return {"$type": f"{type(value).__module__}.{type(value).__qualname__}"}

    def _normalize_text(self, value: str) -> str:
        text = value
        for secret in self._known_secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
                self.redacted = True
        redacted_uri = URI_USERINFO_PATTERN.sub(lambda match: f"{match.group('scheme')}{REDACTED}@", text)
        redacted_token_url = BOT_TOKEN_URL_PATTERN.sub(lambda match: f"{match.group('prefix')}{REDACTED}", redacted_uri)
        if redacted_token_url != text:
            self.redacted = True
        if len(redacted_token_url) > MAX_TEXT_LENGTH:
            self.truncated = True
            return f"{redacted_token_url[:MAX_TEXT_LENGTH]}{TRUNCATED}"
        return redacted_token_url


def normalize_payload(value: Any, known_secrets: Sequence[str] = ()) -> tuple[Any, bool, bool]:
    normalizer = PayloadNormalizer(known_secrets)
    normalized = normalizer.normalize(value)
    return normalized, normalizer.truncated, normalizer.redacted


_PARENT_SPAN_UNSET = object()


def build_event(
    *,
    category: Category,
    name: str,
    phase: Phase,
    summary: str,
    payload: Any = None,
    level: Level = Level.INFO,
    outcome: Outcome | None = None,
    duration_ms: float | None = None,
    error: BaseException | None = None,
    known_secrets: Sequence[str] = (),
    origin: Origin | None = None,
    span_id: str | None = None,
    parent_span_id: str | None | object = _PARENT_SPAN_UNSET,
) -> CapturedEvent:
    context = TRACE_CONTEXT.get()
    current_task_id = task_id()
    effective_origin = origin
    if effective_origin is None:
        if context is None:
            effective_origin = Origin.BACKGROUND if _RUNTIME_READY.is_set() else Origin.STARTUP
        elif context.dispatch_task_id is not None and current_task_id != context.dispatch_task_id:
            effective_origin = Origin.BACKGROUND
        else:
            effective_origin = context.origin
    normalized, payload_truncated, payload_redacted = normalize_payload(payload, known_secrets)
    normalized_summary, summary_truncated, summary_redacted = normalize_payload(summary, known_secrets)
    if not isinstance(normalized_summary, str):
        normalized_summary = TRUNCATED
    effective_parent_span_id = (
        (context.span_id if context is not None else None) if parent_span_id is _PARENT_SPAN_UNSET else parent_span_id
    )
    if effective_parent_span_id is not None and not isinstance(effective_parent_span_id, str):
        raise TypeError("parent_span_id must be a string or None")
    return CapturedEvent(
        monotonic_ns=str(time.monotonic_ns()),
        category=category,
        name=name,
        phase=phase,
        level=level,
        trace_id=context.trace_id if context else None,
        span_id=span_id or (child_span_id() if context else None),
        parent_span_id=effective_parent_span_id,
        task_id=current_task_id,
        update_id=context.update_id if context else None,
        chat_tid=context.chat_tid if context else None,
        duration_ms=duration_ms,
        error_type=type(error).__name__ if error else None,
        origin=effective_origin,
        outcome=outcome,
        summary=normalized_summary,
        payload=normalized,
        truncated=payload_truncated or summary_truncated,
        redacted=payload_redacted or summary_redacted,
    )


class TelemetrySink:
    def __init__(
        self,
        transport: socket.socket,
        run_id: str,
        pid: int,
        *,
        max_records: int = MAX_TELEMETRY_RECORDS,
        max_bytes: int = MAX_TELEMETRY_BYTES,
    ) -> None:
        self._transport = transport
        self._run_id = run_id
        self._pid = pid
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=max_records)
        self._max_bytes = max_bytes
        self._pending_bytes = 0
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._send, name="sophie-debug-telemetry", daemon=True)
        self.dropped_total = 0
        self.recorder_errors = 0

    def start(self) -> None:
        self._thread.start()

    def emit(self, event: CapturedEvent) -> None:
        if self._closed.is_set():
            self.dropped_total += 1
            return
        frame = EventFrame(run_id=self._run_id, pid=self._pid, event=event)
        try:
            encoded = encode_frame(frame, TELEMETRY_FRAME_LIMIT)
        except FrameError:
            truncated_event = event.model_copy(
                update={
                    "payload": {"$truncated": "captured event exceeded the encoded frame limit"},
                    "truncated": True,
                }
            )
            try:
                encoded = encode_frame(
                    EventFrame(run_id=self._run_id, pid=self._pid, event=truncated_event),
                    TELEMETRY_FRAME_LIMIT,
                )
            except FrameError:
                self.recorder_errors += 1
                return
        with self._lock:
            if self._pending_bytes + len(encoded) > self._max_bytes:
                self.dropped_total += 1
                return
            try:
                self._queue.put_nowait(encoded)
            except queue.Full:
                self.dropped_total += 1
                return
            self._pending_bytes += len(encoded)

    def close(self) -> None:
        was_closed = self._closed.is_set()
        self._closed.set()
        if not was_closed:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        with contextlib.suppress(OSError):
            self._transport.shutdown(socket.SHUT_WR)
        self._thread.join(timeout=2)
        with contextlib.suppress(OSError):
            self._transport.close()

    def _send(self) -> None:
        while True:
            encoded = self._queue.get()
            if encoded is None:
                return
            try:
                self._transport.sendall(encoded)
            except OSError:
                self._closed.set()
                self.recorder_errors += 1
                return
            finally:
                with self._lock:
                    self._pending_bytes -= len(encoded)


class TextCapture(io.TextIOBase):
    def __init__(self, original: TextIO, emit_line: Callable[[str], None], *, max_partial: int = 16 * 1024) -> None:
        self._original = original
        self._emit_line = emit_line
        self._max_partial = max_partial
        self._buffer = ""
        self._lock = threading.Lock()

    @property
    def encoding(self) -> str | None:
        return self._original.encoding

    def fileno(self) -> int:
        return self._original.fileno()

    def isatty(self) -> bool:
        return self._original.isatty()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        written = self._original.write(text)
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._emit_line(line)
            if len(self._buffer) >= self._max_partial:
                self._emit_line(f"{self._buffer[: self._max_partial]}{TRUNCATED}")
                self._buffer = ""
        return written

    def flush(self) -> None:
        self._original.flush()
        with self._lock:
            if self._buffer:
                self._emit_line(self._buffer)
                self._buffer = ""
