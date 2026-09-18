from __future__ import annotations

import asyncio
import contextlib
import os
import runpy
import socket
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from debug.actions import WorkerActionError, WorkerActions
from debug.adapters import EventRecorder
from debug.adapters.logs import install_logging_observer
from debug.adapters.middleware import install_middleware_observer
from debug.adapters.mongo import install_mongo_observer
from debug.adapters.redis import install_redis_observer
from debug.adapters.resources import sample_worker_resources
from debug.adapters.telegram import RootCorrelationMiddleware, TelegramRequestObserver
from debug.capture import (
    TRACE_CONTEXT,
    TelemetrySink,
    known_secret_values,
    new_trace_context,
    normalize_payload,
    set_runtime_ready,
)
from debug.i18n import gettext_debug as _
from debug.protocol import (
    CONTROL_FRAME_LIMIT,
    MAX_OUTSTANDING_REQUESTS,
    MAX_PENDING_CONTROL_BYTES,
    Category,
    CommandFrame,
    FrameError,
    Level,
    Origin,
    Outcome,
    Phase,
    ProtocolErrorBody,
    ReplyFrame,
    StatusFrame,
    WorkerState,
    encode_frame,
    read_frame,
    validate_reply_data_size,
)
from sophie_bot.config import CONFIG
from sophie_bot.modes import bot as bot_mode
from sophie_bot.runtime import BotModeRuntime

_MUTATION_OPERATIONS = frozenset(
    {"mongo.insert_one", "mongo.update_one", "mongo.delete_one", "redis.command", "ai_cache.clear"}
)


class WorkerConfigurationError(RuntimeError):
    """The inherited worker environment violates the debug launch boundary."""


class ControlChannel:
    def __init__(
        self,
        transport: socket.socket,
        run_id: str,
        recorder: EventRecorder,
        sink: TelemetrySink,
        known_secrets: tuple[str, ...],
    ) -> None:
        self.transport = transport
        self.run_id = run_id
        self.recorder = recorder
        self.sink = sink
        self.known_secrets = known_secrets
        self.pid = os.getpid()
        self.actions: WorkerActions | None = None
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.writer_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.pending_bytes = 0
        self.command_tasks: set[asyncio.Task[None]] = set()
        self.reader_task: asyncio.Task[None] | None = None
        self.writer_task: asyncio.Task[None] | None = None
        self.resource_task: asyncio.Task[None] | None = None
        self.status_task: asyncio.Task[None] | None = None
        self.ready = False
        self.closed = False

    async def bind(self, runtime: BotModeRuntime) -> None:
        self.transport.setblocking(False)
        self.reader, self.writer = await asyncio.open_connection(sock=self.transport, limit=CONTROL_FRAME_LIMIT)
        self.actions = WorkerActions(runtime, self.sink)
        self.writer_task = asyncio.create_task(self._write_frames(), name="debug-control-writer")
        self.reader_task = asyncio.create_task(self._read_commands(), name="debug-control-reader")
        self.resource_task = asyncio.create_task(sample_worker_resources(self.recorder), name="debug-resource-sampler")
        self.status_task = asyncio.create_task(self._report_status(), name="debug-status-reporter")

    async def mark_ready(self) -> None:
        self.ready = True
        self.recorder.ready = True
        set_runtime_ready(True)
        self.send_status("ready")

    def send_status(self, state: WorkerState, detail: str | None = None) -> bool:
        return self.send_frame(
            StatusFrame(
                run_id=self.run_id,
                pid=self.pid,
                state=state,
                dropped_total=self.sink.dropped_total,
                recorder_errors=self.sink.recorder_errors,
                detail=detail,
            )
        )

    def send_frame(self, frame: StatusFrame | ReplyFrame) -> bool:
        if self.closed:
            return False
        try:
            encoded = encode_frame(frame, CONTROL_FRAME_LIMIT)
        except FrameError:
            self.sink.recorder_errors += 1
            return False
        if self.pending_bytes + len(encoded) > MAX_PENDING_CONTROL_BYTES:
            self.sink.recorder_errors += 1
            return False
        self.pending_bytes += len(encoded)
        self.writer_queue.put_nowait(encoded)
        return True

    async def close(self, state: WorkerState, detail: str | None = None) -> None:
        if self.closed:
            return
        self.ready = False
        self.recorder.ready = False
        set_runtime_ready(False)
        for task in (self.reader_task, self.status_task, self.resource_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self.reader_task, self.status_task, self.resource_task) if task is not None),
            return_exceptions=True,
        )
        if self.command_tasks:
            await asyncio.gather(*tuple(self.command_tasks), return_exceptions=True)
        self.send_status(state, detail)
        if self.writer_task is not None and not self.writer_task.done():
            await self.writer_queue.join()
            self.writer_queue.put_nowait(None)
            await self.writer_task
        self.closed = True
        if self.writer is not None:
            self.writer.close()
            with contextlib.suppress(OSError):
                await self.writer.wait_closed()
        else:
            self.transport.close()

    async def _write_frames(self) -> None:
        assert self.writer is not None
        while True:
            encoded = await self.writer_queue.get()
            if encoded is None:
                self.writer_queue.task_done()
                return
            try:
                self.writer.write(encoded)
                await self.writer.drain()
            except (ConnectionError, OSError):
                self.sink.recorder_errors += 1
                self.writer_queue.task_done()
                while True:
                    try:
                        queued = self.writer_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    if queued is not None:
                        self.pending_bytes -= len(queued)
                    self.writer_queue.task_done()
            else:
                self.pending_bytes -= len(encoded)
                self.writer_queue.task_done()

    async def _read_commands(self) -> None:
        assert self.reader is not None
        try:
            while True:
                frame = await read_frame(self.reader, CONTROL_FRAME_LIMIT)
                if not isinstance(frame, CommandFrame):
                    raise FrameError(_("Worker control channel accepts command frames only"))
                if frame.run_id != self.run_id:
                    raise FrameError(_("Command run ID does not match the live worker"))
                if not self.ready or self.actions is None:
                    self._send_error(frame, "worker_not_ready", _("Worker runtime is not ready"))
                    continue
                if len(self.command_tasks) >= MAX_OUTSTANDING_REQUESTS:
                    self._send_error(frame, "too_many_requests", _("Too many outstanding worker commands"))
                    continue
                task = asyncio.create_task(self._execute(frame), name=f"debug-command-{frame.request_id[:32]}")
                self.command_tasks.add(task)
                task.add_done_callback(self.command_tasks.discard)
        except EOFError:
            return
        except FrameError as error:
            normalized, _truncated, _redacted = normalize_payload(str(error), self.known_secrets)
            self.recorder.emit(
                category=Category.PROCESS,
                name="control.channel_error",
                phase=Phase.INSTANT,
                level=Level.ERROR,
                outcome=Outcome.ERROR,
                summary=str(normalized),
                payload={"error": normalized},
            )
            if self.status_task is not None:
                self.status_task.cancel()
            if self.writer is not None:
                self.writer.close()

    async def _execute(self, frame: CommandFrame) -> None:
        assert self.actions is not None
        context = new_trace_context(origin=Origin.DEBUGGER)
        token = TRACE_CONTEXT.set(context)
        try:
            result = await self.actions.execute(frame.operation, frame.payload)
            try:
                validate_reply_data_size(result)
            except FrameError as error:
                raise WorkerActionError(
                    "result_too_large",
                    _("Worker result exceeded the 1 MiB response limit"),
                    ambiguous=frame.operation in _MUTATION_OPERATIONS,
                ) from error
            self.send_frame(ReplyFrame(request_id=frame.request_id, run_id=self.run_id, result=result))
        except WorkerActionError as error:
            self._send_error(frame, error.code, str(error), ambiguous=error.ambiguous)
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            normalized, _truncated, _redacted = normalize_payload(str(error), self.known_secrets)
            self.recorder.emit(
                category=Category.PROCESS,
                name="control.operation_error",
                phase=Phase.INSTANT,
                level=Level.ERROR,
                outcome=Outcome.ERROR,
                summary=_("Debugger operation failed unexpectedly"),
                payload={"operation": frame.operation, "error_type": type(error).__name__, "error": normalized},
            )
            self._send_error(
                frame,
                "internal_error",
                _("Debugger operation failed"),
                ambiguous=frame.operation in _MUTATION_OPERATIONS,
            )
        finally:
            TRACE_CONTEXT.reset(token)

    def _send_error(self, frame: CommandFrame, code: str, message: str, *, ambiguous: bool = False) -> None:
        normalized, _truncated, _redacted = normalize_payload(message, self.known_secrets)
        safe_message = normalized if isinstance(normalized, str) else _("Debugger operation failed")
        self.send_frame(
            ReplyFrame(
                request_id=frame.request_id,
                run_id=self.run_id,
                error=ProtocolErrorBody(code=code, message=safe_message, ambiguous=ambiguous),
            )
        )

    async def _report_status(self) -> None:
        while True:
            await asyncio.sleep(1)
            self.send_status("ready" if self.ready else "starting")


def _known_secrets() -> tuple[str, ...]:
    values: dict[str, str] = {}
    for name in CONFIG.__class__.model_fields:
        value = getattr(CONFIG, name, None)
        if isinstance(value, str):
            values[name] = value
        elif name in {"sentry_url", "botapi_server"} and value is not None:
            values[name] = str(value)
    for index, provider in enumerate(CONFIG.custom_providers):
        values[f"custom_provider_{index}_api_key"] = provider.api_key
    return known_secret_values(values)


def _validate_configuration() -> tuple[int, int, str]:
    if CONFIG.environment != "development":
        raise WorkerConfigurationError(_("Debugger worker requires ENVIRONMENT=development"))
    if CONFIG.mode != "bot":
        raise WorkerConfigurationError(_("Debugger worker requires MODE=bot"))
    if CONFIG.webhooks_enable:
        raise WorkerConfigurationError(_("Debugger worker requires polling; WEBHOOKS_ENABLE must be false"))
    if CONFIG.run_migrations_on_startup:
        raise WorkerConfigurationError(_("Debugger worker will not run startup migrations"))
    if CONFIG.dev_reload:
        raise WorkerConfigurationError(_("Debugger supervisor owns reloads; DEV_RELOAD must be false"))
    try:
        telemetry_fd = int(os.environ["SOPHIE_DEBUG_TELEMETRY_FD"])
        control_fd = int(os.environ["SOPHIE_DEBUG_CONTROL_FD"])
        run_id = os.environ["SOPHIE_DEBUG_RUN_ID"]
    except (KeyError, ValueError) as error:
        raise WorkerConfigurationError(_("Debugger worker IPC environment is incomplete")) from error
    if not run_id:
        raise WorkerConfigurationError(_("Debugger worker run ID is empty"))
    return telemetry_fd, control_fd, run_id


def _send_sync_status(
    transport: socket.socket,
    run_id: str,
    state: WorkerState,
    *,
    dropped_total: int = 0,
    recorder_errors: int = 0,
    detail: str | None = None,
) -> None:
    frame = StatusFrame(
        run_id=run_id,
        pid=os.getpid(),
        state=state,
        dropped_total=dropped_total,
        recorder_errors=recorder_errors,
        detail=detail,
    )
    transport.sendall(encode_frame(frame, CONTROL_FRAME_LIMIT))


def _install_lifecycle(
    controller: ControlChannel,
    recorder: EventRecorder,
) -> Callable[[], None]:
    original_build_runtime = bot_mode.build_bot_runtime
    original_prepare_runtime = bot_mode._prepare_runtime
    request_observer = TelegramRequestObserver(recorder)
    correlation = RootCorrelationMiddleware(recorder)

    async def close_control(state: WorkerState, detail: str | None = None) -> None:
        try:
            await controller.close(state, detail)
        except (OSError, RuntimeError):
            # Cleanup instrumentation must not replace the bot's return, failure, or cancellation.
            controller.sink.recorder_errors += 1

    @asynccontextmanager
    async def build_bot_runtime(*args: Any, **kwargs: Any) -> Any:
        async with original_build_runtime(*args, **kwargs) as runtime:
            runtime.services.bot.session.middleware.register(request_observer)
            await controller.bind(runtime)
            try:
                yield runtime
            except asyncio.CancelledError:
                await close_control("stopped")
                raise
            except BaseException as error:
                await close_control(
                    "failed",
                    _("Bot runtime failed: {error_type}").format(error_type=type(error).__name__),
                )
                raise
            else:
                await close_control("stopped")
            finally:
                runtime.services.bot.session.middleware.unregister(request_observer)

    async def prepare_runtime(runtime: BotModeRuntime) -> None:
        await original_prepare_runtime(runtime)
        manager = runtime.dispatcher.update.outer_middleware
        installed = tuple(manager)
        for middleware in installed:
            manager.unregister(middleware)
        manager.register(correlation)
        for middleware in installed:
            manager.register(middleware)
        await controller.mark_ready()

    setattr(bot_mode, "build_bot_runtime", build_bot_runtime)  # noqa: B010
    setattr(bot_mode, "_prepare_runtime", prepare_runtime)  # noqa: B010

    def restore() -> None:
        setattr(bot_mode, "_prepare_runtime", original_prepare_runtime)  # noqa: B010
        setattr(bot_mode, "build_bot_runtime", original_build_runtime)  # noqa: B010

    return restore


def main() -> None:
    set_runtime_ready(False)
    telemetry_fd, control_fd, run_id = _validate_configuration()
    telemetry_socket = socket.socket(fileno=telemetry_fd)
    control_socket = socket.socket(fileno=control_fd)
    sink = TelemetrySink(telemetry_socket, run_id, os.getpid())
    secrets = _known_secrets()
    recorder = EventRecorder(sink, secrets)
    controller = ControlChannel(control_socket, run_id, recorder, sink, secrets)
    restores: list[Callable[[], None]] = []
    sink.start()
    try:
        _send_sync_status(control_socket, run_id, "starting")
        _listener, restore_mongo = install_mongo_observer(recorder)
        restores.append(restore_mongo)
        restores.append(install_redis_observer(recorder))
        restores.append(install_middleware_observer(recorder))
        restores.append(_install_lifecycle(controller, recorder))
        restores.append(install_logging_observer(recorder))
        runpy.run_module("sophie_bot", run_name="__main__")
    except BaseException as error:
        if not controller.closed and controller.writer is None:
            normalized, _truncated, _redacted = normalize_payload(str(error), secrets)
            detail = normalized if isinstance(normalized, str) else type(error).__name__
            with contextlib.suppress(OSError, FrameError):
                _send_sync_status(
                    control_socket,
                    run_id,
                    "failed",
                    dropped_total=sink.dropped_total,
                    recorder_errors=sink.recorder_errors,
                    detail=detail,
                )
        raise
    finally:
        for restore in reversed(restores):
            with contextlib.suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
                restore()
        with contextlib.suppress(OSError):
            sink.close()
        if not controller.closed:
            with contextlib.suppress(OSError):
                control_socket.close()


if __name__ == "__main__":
    main()
