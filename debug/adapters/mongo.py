from __future__ import annotations

import inspect
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pymongo.monitoring import CommandFailedEvent, CommandListener, CommandStartedEvent, CommandSucceededEvent

import sophie_bot.services.db as database_service
from debug.adapters import EventRecorder
from debug.capture import TRACE_CONTEXT, TraceContext, child_span_id, task_id
from debug.i18n import gettext_debug as _
from debug.protocol import Category, Level, Origin, Outcome, Phase

_MAX_PENDING_COMMANDS = 4_096


class MongoCommandFailure(RuntimeError):
    """Type marker for a failed wire command reported by PyMongo."""


@dataclass(frozen=True, slots=True)
class _PendingCommand:
    context: TraceContext | None
    origin: Origin
    span_id: str
    parent_span_id: str | None
    task_id: str | None


def _connection_identity(event: Any) -> dict[str, Any]:
    connection = event.connection_id
    host = connection[0] if isinstance(connection, tuple) and connection else None
    port = connection[1] if isinstance(connection, tuple) and len(connection) > 1 else None
    return {
        "host": host,
        "port": port,
        "service_id": event.service_id,
        "server_connection_id": event.server_connection_id,
    }


def _pending_key(event: Any) -> tuple[Any, str | None, int]:
    service_id = str(event.service_id) if event.service_id is not None else None
    return event.connection_id, service_id, event.request_id


class DebugCommandListener(CommandListener):
    """PyMongo's synchronous callback listener; it only snapshots and enqueues."""

    def __init__(self, recorder: EventRecorder) -> None:
        self._recorder = recorder
        self._pending: OrderedDict[tuple[Any, str | None, int], _PendingCommand] = OrderedDict()
        self._lock = threading.Lock()

    def started(self, event: CommandStartedEvent) -> None:
        context = TRACE_CONTEXT.get()
        current_task_id = task_id()
        if context is None:
            origin = Origin.BACKGROUND if self._recorder.ready else Origin.STARTUP
            parent_span_id = None
        elif context.dispatch_task_id is not None and current_task_id != context.dispatch_task_id:
            origin = Origin.BACKGROUND
            parent_span_id = context.span_id
        else:
            origin = context.origin
            parent_span_id = context.span_id
        pending = _PendingCommand(
            context=context,
            origin=origin,
            span_id=child_span_id(),
            parent_span_id=parent_span_id,
            task_id=current_task_id,
        )
        with self._lock:
            self._pending[_pending_key(event)] = pending
            if len(self._pending) > _MAX_PENDING_COMMANDS:
                self._pending.popitem(last=False)
                self._recorder.sink.recorder_errors += 1
        self._recorder.emit_in_context(
            context,
            category=Category.MONGO,
            name=event.command_name,
            phase=Phase.START,
            origin=origin,
            span_id=pending.span_id,
            parent_span_id=parent_span_id,
            _task_id=pending.task_id,
            summary=_("MongoDB {command} started").format(command=event.command_name),
            payload={
                "database": event.database_name,
                "command": event.command,
                "request_id": event.request_id,
                "operation_id": event.operation_id,
                "connection": _connection_identity(event),
            },
        )

    def succeeded(self, event: CommandSucceededEvent) -> None:
        pending = self._take(event)
        self._recorder.emit_in_context(
            pending.context,
            category=Category.MONGO,
            name=event.command_name,
            phase=Phase.FINISH,
            origin=pending.origin,
            span_id=pending.span_id,
            parent_span_id=pending.parent_span_id,
            _task_id=pending.task_id,
            outcome=Outcome.OK,
            duration_ms=event.duration_micros / 1_000,
            summary=_("MongoDB {command} succeeded").format(command=event.command_name),
            payload={
                "database": event.database_name,
                "reply": event.reply,
                "request_id": event.request_id,
                "operation_id": event.operation_id,
                "connection": _connection_identity(event),
            },
        )

    def failed(self, event: CommandFailedEvent) -> None:
        pending = self._take(event)
        self._recorder.emit_in_context(
            pending.context,
            category=Category.MONGO,
            name=event.command_name,
            phase=Phase.FINISH,
            origin=pending.origin,
            span_id=pending.span_id,
            parent_span_id=pending.parent_span_id,
            _task_id=pending.task_id,
            outcome=Outcome.ERROR,
            level=Level.ERROR,
            error=MongoCommandFailure(),
            duration_ms=event.duration_micros / 1_000,
            summary=_("MongoDB {command} failed").format(command=event.command_name),
            payload={
                "database": event.database_name,
                "failure": event.failure,
                "request_id": event.request_id,
                "operation_id": event.operation_id,
                "connection": _connection_identity(event),
            },
        )

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()

    def _take(self, event: CommandSucceededEvent | CommandFailedEvent) -> _PendingCommand:
        with self._lock:
            pending = self._pending.pop(_pending_key(event), None)
        if pending is not None:
            return pending
        self._recorder.sink.recorder_errors += 1
        return _PendingCommand(
            context=None,
            origin=Origin.BACKGROUND if self._recorder.ready else Origin.STARTUP,
            span_id=child_span_id(),
            parent_span_id=None,
            task_id=None,
        )


def install_mongo_observer(recorder: EventRecorder) -> tuple[DebugCommandListener, Callable[[], None]]:
    original_client = database_service.AsyncMongoClient
    signature = inspect.signature(original_client)
    if not any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        raise RuntimeError(_("Installed AsyncMongoClient cannot accept event listeners"))
    listener = DebugCommandListener(recorder)

    def observed_client(*args: Any, **kwargs: Any) -> Any:
        supplied = kwargs.get("event_listeners")
        listeners = list(supplied) if supplied is not None else []
        listeners.append(listener)
        kwargs["event_listeners"] = listeners
        return original_client(*args, **kwargs)

    setattr(database_service, "AsyncMongoClient", observed_client)  # noqa: B010

    def restore() -> None:
        listener.clear()
        setattr(database_service, "AsyncMongoClient", original_client)  # noqa: B010

    return listener, restore
