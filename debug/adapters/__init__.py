from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from debug.capture import TRACE_CONTEXT, TelemetrySink, TraceContext, build_event
from debug.protocol import Origin

_UNSET = object()


class EventRecorder:
    """Small observer-isolation boundary shared by worker-local adapters."""

    def __init__(self, sink: TelemetrySink, known_secrets: Sequence[str] = ()) -> None:
        self.sink = sink
        self.known_secrets = tuple(known_secrets)
        self.ready = False

    def emit(self, **fields: Any) -> None:
        task_override = fields.pop("_task_id", _UNSET)
        if fields.get("origin") is None and TRACE_CONTEXT.get() is None:
            fields["origin"] = Origin.BACKGROUND if self.ready else Origin.STARTUP
        try:
            event = build_event(known_secrets=self.known_secrets, **fields)
            if task_override is not _UNSET:
                event = event.model_copy(update={"task_id": task_override})
            self.sink.emit(event)
        except (OSError, OverflowError, RuntimeError, TypeError, UnicodeError, ValueError):
            # Observers must never replace an application result or exception.
            self.sink.recorder_errors += 1

    def emit_in_context(self, context: TraceContext | None, **fields: Any) -> None:
        if context is None:
            self.emit(**fields)
            return
        token = TRACE_CONTEXT.set(context)
        try:
            self.emit(**fields)
        finally:
            TRACE_CONTEXT.reset(token)
