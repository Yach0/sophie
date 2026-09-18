from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import secrets
import signal
import socket
import sys
import tempfile
import time
import uuid
import webbrowser
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import psutil
import uvicorn
from dotenv import dotenv_values
from pydantic import JsonValue
from watchfiles import Change, awatch

from debug.capture import build_event, known_secret_values, normalize_payload
from debug.collector import CollectorState, ControlCapacityError, create_app
from debug.i18n import gettext_debug as _
from debug.protocol import (
    CONTROL_FRAME_LIMIT,
    MAX_OUTSTANDING_REQUESTS,
    MAX_PENDING_CONTROL_BYTES,
    TELEMETRY_FRAME_LIMIT,
    Category,
    CommandFrame,
    EventFrame,
    FrameError,
    Level,
    Origin,
    Outcome,
    Phase,
    ReplyFrame,
    StatusFrame,
    encode_frame,
    read_frame,
)

PLACEHOLDER_TOKENS = {"12345:ABCDEFG", "12345:REPLACE_WITH_DEVELOPMENT_BOT_TOKEN"}
REQUIRED_CONFIG_FIELDS = (
    "ENVIRONMENT",
    "TOKEN",
    "MONGO_HOST",
    "MONGO_PORT",
    "MONGO_DB",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB_STATES",
    "REDIS_DB_FSM",
    "REDIS_DB_SCHEDULE",
    "RUN_MIGRATIONS_ON_STARTUP",
)
INHERITED_ENVIRONMENT_KEYS = {
    "HOME",
    "LANG",
    "PATH",
    "PYTHONPATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "VIRTUAL_ENV",
}
FRONTEND_ENVIRONMENT_KEYS = INHERITED_ENVIRONMENT_KEYS | {"BUN_INSTALL", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"}
INTERPOLATION_PATTERN = re.compile(r"\$\{[^}]+}")
WORKER_DEBUG_PATHS = {
    Path("debug/worker.py"),
    Path("debug/actions.py"),
}
PARENT_DEBUG_PATHS = {
    Path("debug/__init__.py"),
    Path("debug/__main__.py"),
    Path("debug/supervisor.py"),
    Path("debug/collector.py"),
    Path("debug/protocol.py"),
    Path("debug/capture.py"),
    Path("debug/i18n.py"),
}


class DebugConfigError(ValueError):
    """The dedicated debugger configuration is unsafe or incomplete."""

    def __init__(self, message: str) -> None:
        super().__init__(_(message))


class DebugLaunchError(RuntimeError):
    """A required debugger process or service could not start."""

    def __init__(self, message: str) -> None:
        super().__init__(_(message))


@dataclass(frozen=True, slots=True)
class DebugConfig:
    path: Path
    values: Mapping[str, str]

    @property
    def sanitized_targets(self) -> dict[str, str | int]:
        return {
            "environment": self.values["ENVIRONMENT"],
            "mongo_host": _sanitize_target(self.values["MONGO_HOST"]),
            "mongo_port": int(self.values["MONGO_PORT"]),
            "mongo_db": self.values["MONGO_DB"],
            "redis_host": _sanitize_target(self.values["REDIS_HOST"]),
            "redis_port": int(self.values["REDIS_PORT"]),
            "redis_db_states": int(self.values["REDIS_DB_STATES"]),
        }

    @property
    def known_secrets(self) -> tuple[str, ...]:
        return known_secret_values(self.values)


def _sanitize_target(value: str) -> str:
    sanitized, _truncated, _redacted = normalize_payload(value)
    return sanitized if isinstance(sanitized, str) else "[REDACTED]"


def preflight_config(path: str | Path) -> DebugConfig:
    selected_path = Path(path).expanduser().resolve()
    try:
        raw_config = selected_path.read_text(encoding="utf-8")
    except OSError as error:
        raise DebugConfigError(f"Cannot read debugger config: {selected_path}") from error

    if INTERPOLATION_PATTERN.search(raw_config):
        raise DebugConfigError("Debugger config must contain literal values; ${...} interpolation is not allowed")

    parsed = dotenv_values(selected_path, interpolate=False)
    values = {key: value for key, value in parsed.items() if value is not None}
    missing = sorted(field for field in REQUIRED_CONFIG_FIELDS if not values.get(field, "").strip())
    if missing:
        raise DebugConfigError(f"Debugger config is missing required fields: {', '.join(missing)}")
    if values["ENVIRONMENT"] != "development":
        raise DebugConfigError("Debugger config requires ENVIRONMENT=development")
    if values["TOKEN"] in PLACEHOLDER_TOKENS:
        raise DebugConfigError("Debugger config TOKEN is still the placeholder value")
    if values["RUN_MIGRATIONS_ON_STARTUP"] != "false":
        raise DebugConfigError("Debugger config requires RUN_MIGRATIONS_ON_STARTUP=false")
    if values.get("WEBHOOKS_ENABLE", "false").lower() == "true":
        raise DebugConfigError("Debugger launcher does not support webhooks; use a development polling bot")

    for field_name in ("MONGO_PORT", "REDIS_PORT", "REDIS_DB_STATES", "REDIS_DB_FSM", "REDIS_DB_SCHEDULE"):
        try:
            int(values[field_name])
        except ValueError as error:
            raise DebugConfigError(f"Debugger config field {field_name} must be an integer") from error

    return DebugConfig(path=selected_path, values=values)


def sanitized_child_environment(config: DebugConfig, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in INHERITED_ENVIRONMENT_KEYS or key.startswith("LC_")
    }
    environment.update(
        {
            "DEV_RELOAD": "false",
            "MODE": "bot",
            "PYTHONUNBUFFERED": "1",
            "SOPHIE_CONFIG_FILE": str(config.path),
        }
    )
    if extra:
        environment.update(extra)
    return environment


def sanitized_frontend_environment(port: int, api_origin: str) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in FRONTEND_ENVIRONMENT_KEYS or key.startswith("LC_")
    }
    environment.update(
        {
            "NO_PROXY": "127.0.0.1,localhost",
            "SOPHIE_DEBUG_API_ORIGIN": api_origin,
            "SOPHIE_DEBUG_UI_PORT": str(port),
        }
    )
    return environment


class SignalFreeServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Any:
        yield


@dataclass(slots=True)
class WorkerRun:
    run_id: str
    channel_generation: int
    process: asyncio.subprocess.Process
    telemetry_reader: asyncio.StreamReader
    telemetry_writer: asyncio.StreamWriter
    control_reader: asyncio.StreamReader
    control_writer: asyncio.StreamWriter
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    stopping: bool = False


class Supervisor:
    def __init__(
        self,
        *,
        root: Path,
        config_path: str | Path,
        port: int,
        ui_port: int,
        open_browser: bool,
        worker_module: str = "debug.worker",
    ) -> None:
        self.root = root.resolve()
        selected_config = Path(config_path).expanduser()
        self.config_path = selected_config if selected_config.is_absolute() else self.root / selected_config
        self.port = port
        self.ui_port = ui_port
        self.open_browser = open_browser
        self.worker_module = worker_module
        self.api_origin = f"http://127.0.0.1:{port}"
        self.ui_origin = f"http://127.0.0.1:{ui_port}"
        self.frontend_dir = self.root / "debug" / "frontend"
        self.data_dir = self.root / "data"
        self.lock_path = self.data_dir / "debug.lock"
        self.session_path = self.data_dir / "debug-session.json"
        self.lock_fd: int | None = None
        self.config: DebugConfig | None = None
        self.state: CollectorState | None = None
        self.worker: WorkerRun | None = None
        self.vite: asyncio.subprocess.Process | None = None
        self.server: SignalFreeServer | None = None
        self.collector_socket: socket.socket | None = None
        self.server_task: asyncio.Task[Any] | None = None
        self.vite_tasks: set[asyncio.Task[Any]] = set()
        self.watcher_task: asyncio.Task[Any] | None = None
        self.resource_task: asyncio.Task[Any] | None = None
        self.stop_event = asyncio.Event()
        self.signal_handlers_installed = False
        self.reload_lock = asyncio.Lock()
        self.session_id = uuid.uuid4().hex
        self.bearer_token = secrets.token_urlsafe(32)
        self.browser_credential = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self._pending_replies: dict[str, asyncio.Future[ReplyFrame]] = {}
        self._pending_control_bytes = 0

    async def run(self) -> None:
        self._acquire_lock()
        try:
            self.config = preflight_config(self.config_path)
            self.state = CollectorState(
                session_id=self.session_id,
                bearer_token=self.bearer_token,
                browser_credential=self.browser_credential,
                csrf_token=self.csrf_token,
                api_origin=self.api_origin,
                ui_origin=self.ui_origin,
                sanitized_targets=self.config.sanitized_targets,
                capabilities={
                    "mongo": True,
                    "redis": True,
                    "redis_pubsub": False,
                    "telegram": True,
                    "middleware": True,
                    "logs": True,
                    "python_print_capture": True,
                    "native_fd_live_capture": False,
                    "ai_cache": True,
                    "mutations": True,
                },
            )
            self.state.restart_worker = self.restart_worker
            self.state.control_dispatch = self.dispatch_command
            self.state.known_secrets = self.config.known_secrets
            self._write_session_file()
            self._install_signal_handlers()
            await self._install_frontend()
            await self._start_collector()
            await self._start_vite()
            self.resource_task = asyncio.create_task(self._sample_parent_resources(), name="debug-parent-resources")
            self.watcher_task = asyncio.create_task(self._watch_changes(), name="debug-file-watcher")
            if self.open_browser:
                opened = await asyncio.to_thread(webbrowser.open, f"{self.ui_origin}/#token={self.bearer_token}")
                if not opened:
                    message = _("Open {ui_url}/ and use credentials from {session_path}").format(
                        ui_url=self.ui_origin,
                        session_path=self.session_path,
                    )
                    print(message, file=sys.stderr)
            else:
                print(_("Debugger UI: {ui_url}/").format(ui_url=self.ui_origin), file=sys.stderr)
                print(_("Agent credentials: {session_path}").format(session_path=self.session_path), file=sys.stderr)
            if not self.stop_event.is_set():
                await self._start_worker()
            await self._wait_until_stopped()
        finally:
            await self.shutdown()

    def _acquire_lock(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.lock_path.is_symlink():
            raise DebugLaunchError(f"Refusing symlink lock path: {self.lock_path}")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            lock_fd = os.open(self.lock_path, flags, 0o600)
            os.fchmod(lock_fd, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if "lock_fd" in locals():
                os.close(lock_fd)
            raise DebugLaunchError("Another debugger is already running for this checkout") from error
        self.lock_fd = lock_fd

    def _write_session_file(self) -> None:
        if self.session_path.is_symlink():
            raise DebugLaunchError(f"Refusing symlink session path: {self.session_path}")
        content = {
            "api_url": self.api_origin,
            "ui_url": self.ui_origin,
            "token": self.bearer_token,
            "session_id": self.session_id,
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=".debug-session-", dir=self.data_dir)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as session_file:
                json.dump(content, session_file, separators=(",", ":"))
                session_file.flush()
                os.fsync(session_file.fileno())
            os.replace(temporary_name, self.session_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary_name)
            raise

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, self.stop_event.set)
        self.signal_handlers_installed = True

    async def _install_frontend(self) -> None:
        if not self.frontend_dir.is_dir():
            raise DebugLaunchError(f"Debugger frontend is missing: {self.frontend_dir}")
        environment = sanitized_frontend_environment(self.ui_port, self.api_origin)
        try:
            process = await asyncio.create_subprocess_exec(
                "bun",
                "install",
                "--frozen-lockfile",
                cwd=self.frontend_dir,
                env=environment,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise DebugLaunchError("Bun is required to run the debugger frontend") from error
        return_code = await self._wait_startup_process(process)
        if return_code != 0:
            raise DebugLaunchError(f"Frontend dependency install failed with exit code {return_code}")

    async def _wait_startup_process(self, process: asyncio.subprocess.Process) -> int:
        process_task = asyncio.create_task(process.wait())
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, _pending = await asyncio.wait({process_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done and self.stop_event.is_set() and process.returncode is None:
            await self._signal_and_wait(process, signal.SIGTERM, 2)
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            process_task.cancel()
            await asyncio.gather(process_task, return_exceptions=True)
            raise DebugLaunchError("Debugger startup interrupted")
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        return await process_task

    async def _start_collector(self) -> None:
        assert self.state is not None
        app = create_app(self.state)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            listener.setblocking(False)
        except OSError as error:
            listener.close()
            raise DebugLaunchError(f"Collector port {self.port} is unavailable") from error
        self.collector_socket = listener
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
        )
        self.server = SignalFreeServer(config)
        self.server_task = asyncio.create_task(self.server.serve(sockets=[listener]), name="debug-collector")
        await self._wait_http(self.api_origin, authorization=True, process_task=self.server_task)

    async def _start_vite(self) -> None:
        environment = sanitized_frontend_environment(self.ui_port, self.api_origin)
        try:
            self.vite = await asyncio.create_subprocess_exec(
                "bun",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ui_port),
                cwd=self.frontend_dir,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise DebugLaunchError("Bun is required to run the debugger frontend") from error
        self.vite_tasks = {
            asyncio.create_task(self._forward_pipe(self.vite.stdout, sys.stdout), name="debug-vite-stdout"),
            asyncio.create_task(self._forward_pipe(self.vite.stderr, sys.stderr), name="debug-vite-stderr"),
        }
        await self._wait_http(
            self.ui_origin,
            authorization=False,
            process_task=self.server_task,
            process=self.vite,
        )

    async def _wait_http(
        self,
        origin: str,
        *,
        authorization: bool,
        process_task: asyncio.Task[Any] | None = None,
        process: asyncio.subprocess.Process | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {self.bearer_token}"} if authorization else None
        deadline = asyncio.get_running_loop().time() + 20
        async with httpx.AsyncClient(trust_env=False, timeout=0.5) as client:
            while asyncio.get_running_loop().time() < deadline:
                if self.stop_event.is_set():
                    raise DebugLaunchError("Debugger startup interrupted")
                if process_task is not None and process_task.done():
                    exception = process_task.exception()
                    raise DebugLaunchError(f"Collector stopped during startup: {exception or 'port unavailable'}")
                if process is not None and process.returncode is not None:
                    raise DebugLaunchError(f"Vite stopped during startup with exit code {process.returncode}")
                try:
                    response = await client.get(
                        f"{origin}/api/v1/session" if authorization else origin,
                        headers=headers,
                    )
                    if authorization:
                        if response.status_code == 200 and response.json().get("session_id") == self.session_id:
                            return
                    elif response.status_code == 200:
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(0.1)
        raise DebugLaunchError(f"Service did not become ready at {origin}")

    async def _start_worker(self) -> None:
        assert self.config is not None
        assert self.state is not None
        config = preflight_config(self.config.path)
        self.config = config
        self.state.sanitized_targets = config.sanitized_targets
        self.state.known_secrets = config.known_secrets
        run_id = uuid.uuid4().hex
        telemetry_parent, telemetry_child = socket.socketpair()
        control_parent, control_child = socket.socketpair()
        for child_socket in (telemetry_child, control_child):
            child_socket.set_inheritable(True)
        environment = sanitized_child_environment(
            config,
            {
                "SOPHIE_DEBUG_CONTROL_FD": str(control_child.fileno()),
                "SOPHIE_DEBUG_RUN_ID": run_id,
                "SOPHIE_DEBUG_TELEMETRY_FD": str(telemetry_child.fileno()),
            },
        )
        self.state.run_id = run_id
        self.state.worker_pid = None
        self.state.state = "starting"
        self.state.detail = None
        await self.state.notify_status_change()
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                self.worker_module,
                cwd=self.root,
                env=environment,
                pass_fds=(telemetry_child.fileno(), control_child.fileno()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, asyncio.CancelledError):
            telemetry_parent.close()
            control_parent.close()
            raise
        finally:
            telemetry_child.close()
            control_child.close()
        telemetry_parent.setblocking(False)
        control_parent.setblocking(False)
        telemetry_reader, telemetry_writer = await asyncio.open_connection(
            sock=telemetry_parent,
            limit=TELEMETRY_FRAME_LIMIT,
        )
        control_reader, control_writer = await asyncio.open_connection(sock=control_parent, limit=CONTROL_FRAME_LIMIT)
        channel_generation = await self.state.set_worker_channel(run_id, process.pid)
        worker = WorkerRun(
            run_id=run_id,
            channel_generation=channel_generation,
            process=process,
            telemetry_reader=telemetry_reader,
            telemetry_writer=telemetry_writer,
            control_reader=control_reader,
            control_writer=control_writer,
        )
        self.worker = worker
        self.state.worker_pid = process.pid
        stderr_tail: deque[bytes] = deque()
        worker.tasks.update(
            {
                asyncio.create_task(self._read_telemetry(worker), name=f"debug-telemetry-{run_id}"),
                asyncio.create_task(self._read_control(worker), name=f"debug-control-{run_id}"),
                asyncio.create_task(
                    self._forward_pipe(process.stdout, sys.stdout), name=f"debug-worker-stdout-{run_id}"
                ),
                asyncio.create_task(
                    self._forward_worker_stderr(process.stderr, stderr_tail, config.known_secrets),
                    name=f"debug-worker-stderr-{run_id}",
                ),
                asyncio.create_task(self._wait_worker(worker, stderr_tail), name=f"debug-worker-wait-{run_id}"),
            }
        )

    async def _read_telemetry(self, worker: WorkerRun) -> None:
        assert self.state is not None
        try:
            while True:
                frame = await read_frame(worker.telemetry_reader, TELEMETRY_FRAME_LIMIT)
                if not isinstance(frame, EventFrame):
                    raise FrameError("Telemetry channel received a non-event frame")
                if frame.run_id != worker.run_id:
                    raise FrameError("Telemetry frame run ID does not match the channel")
                await self.state.event_store.append(
                    frame.event,
                    session_id=self.session_id,
                    run_id=worker.run_id,
                    pid=frame.pid,
                )
                if (
                    frame.event.category == Category.RESOURCE
                    and frame.event.name == "worker.sample"
                    and isinstance(frame.event.payload, dict)
                ):
                    sample = dict(frame.event.payload)
                    sample["run_id"] = worker.run_id
                    self.state.resources["worker"].append(sample)
        except EOFError:
            return
        except FrameError as error:
            await self._process_diagnostic(worker, "telemetry.channel_error", str(error), Level.ERROR)
            worker.telemetry_writer.close()

    async def _read_control(self, worker: WorkerRun) -> None:
        assert self.state is not None
        try:
            while True:
                frame = await read_frame(worker.control_reader, CONTROL_FRAME_LIMIT)
                if frame.run_id != worker.run_id:
                    raise FrameError("Control frame run ID does not match the channel")
                if isinstance(frame, StatusFrame):
                    self.state.state = frame.state
                    self.state.dropped_total = frame.dropped_total
                    self.state.recorder_errors = frame.recorder_errors
                    self.state.detail = frame.detail
                    await self.state.notify_status_change()
                elif isinstance(frame, ReplyFrame):
                    future = self._pending_replies.get(frame.request_id)
                    if future is not None and not future.done():
                        future.set_result(frame)
                else:
                    raise FrameError("Control channel received an invalid frame type")
        except EOFError:
            return
        except FrameError as error:
            await self._process_diagnostic(worker, "control.channel_error", str(error), Level.ERROR)
            worker.control_writer.close()
        finally:
            await self.state.worker_channel_closed(worker.run_id)

    async def _wait_worker(self, worker: WorkerRun, stderr_tail: deque[bytes]) -> None:
        assert self.state is not None
        return_code = await worker.process.wait()
        if worker.stopping:
            return
        tail = b"".join(stderr_tail)[-64 * 1024 :].decode("utf-8", errors="replace")
        self.state.state = "failed"
        self.state.detail = f"Worker exited with code {return_code}"
        await self.state.worker_channel_closed(worker.run_id)
        await self.state.notify_status_change()
        await self._process_diagnostic(
            worker,
            "worker.exit",
            self.state.detail,
            Level.ERROR,
            payload={"return_code": return_code, "stderr_tail": tail},
        )

    async def _process_diagnostic(
        self,
        worker: WorkerRun,
        name: str,
        summary: str,
        level: Level,
        payload: Any = None,
    ) -> None:
        assert self.state is not None
        event = build_event(
            category=Category.PROCESS,
            name=name,
            phase=Phase.INSTANT,
            level=level,
            origin=Origin.DEBUGGER,
            outcome=Outcome.ERROR if level == Level.ERROR else None,
            summary=summary,
            payload=payload,
            known_secrets=self.config.known_secrets if self.config else (),
        )
        await self.state.event_store.append(
            event,
            session_id=self.session_id,
            run_id=worker.run_id,
            pid=worker.process.pid,
        )

    async def _forward_pipe(self, stream: asyncio.StreamReader | None, target: Any) -> None:
        if stream is None:
            return
        while chunk := await stream.read(16 * 1024):
            buffer = getattr(target, "buffer", target)
            buffer.write(chunk)
            buffer.flush()

    async def _forward_worker_stderr(
        self,
        stream: asyncio.StreamReader | None,
        tail: deque[bytes],
        known_secrets: tuple[str, ...],
    ) -> None:
        if stream is None:
            return
        size = 0
        while chunk := await stream.read(16 * 1024):
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
            text = chunk.decode("utf-8", errors="replace")
            normalized, _truncated, _redacted = normalize_payload(text, known_secrets)
            encoded = str(normalized).encode("utf-8")
            tail.append(encoded)
            size += len(encoded)
            while tail and size > 64 * 1024:
                size -= len(tail.popleft())

    async def dispatch_command(
        self,
        request_id: str,
        run_id: str,
        operation: str,
        channel_generation: int,
        payload: dict[str, JsonValue],
    ) -> ReplyFrame:
        future: asyncio.Future[ReplyFrame] | None = None
        encoded_size = 0
        try:
            async with self.reload_lock:
                worker = self.worker
                if (
                    worker is None
                    or worker.process.returncode is not None
                    or worker.control_writer.is_closing()
                    or worker.run_id != run_id
                    or worker.channel_generation != channel_generation
                ):
                    raise ConnectionError("Worker control channel is not available for this run")
                if request_id in self._pending_replies or len(self._pending_replies) >= MAX_OUTSTANDING_REQUESTS:
                    raise ControlCapacityError("Worker control request capacity is exhausted")
                frame = CommandFrame(
                    request_id=request_id,
                    run_id=run_id,
                    operation=operation,
                    payload=payload,
                )
                try:
                    encoded = encode_frame(frame, CONTROL_FRAME_LIMIT)
                except FrameError as error:
                    raise ControlCapacityError(str(error)) from error
                encoded_size = len(encoded)
                if self._pending_control_bytes + encoded_size > MAX_PENDING_CONTROL_BYTES:
                    raise ControlCapacityError("Worker control output capacity is exhausted")
                future = asyncio.get_running_loop().create_future()
                self._pending_replies[request_id] = future
                self._pending_control_bytes += encoded_size
                worker.control_writer.write(encoded)
                await worker.control_writer.drain()
            assert future is not None
            return await future
        except (BrokenPipeError, ConnectionError, OSError) as error:
            raise ConnectionError("Worker control channel closed") from error
        finally:
            if future is not None and self._pending_replies.get(request_id) is future:
                self._pending_replies.pop(request_id, None)
                self._pending_control_bytes -= encoded_size

    async def restart_worker(self) -> None:
        async with self.reload_lock:
            assert self.state is not None
            assert self.config is not None
            if self.state.restart_required:
                self.state.detail = _("Debugger parent code changed; restart make dev to load it")
                await self.state.notify_status_change()
                return
            try:
                preflight_config(self.config.path)
            except DebugConfigError as error:
                self.state.detail = str(error)
                await self.state.notify_status_change()
                return
            self.state.state = "reloading"
            await self.state.notify_status_change()
            await self._stop_worker()
            await self._start_worker()

    async def _stop_worker(self) -> None:
        worker = self.worker
        if worker is None:
            return
        assert self.state is not None
        worker.stopping = True
        process = worker.process
        if process.returncode is None:
            await self._signal_and_wait(process, signal.SIGINT, 10)
        if process.returncode is None:
            await self._signal_and_wait(process, signal.SIGTERM, 2)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        worker.control_writer.close()
        worker.telemetry_writer.close()
        with contextlib.suppress(Exception):
            await worker.control_writer.wait_closed()
        with contextlib.suppress(Exception):
            await worker.telemetry_writer.wait_closed()
        for task in worker.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*worker.tasks, return_exceptions=True)
        await self.state.worker_channel_closed(worker.run_id)
        for future in tuple(self._pending_replies.values()):
            if not future.done():
                future.set_exception(ConnectionError("Worker control channel closed"))
        self.worker = None

    async def _signal_and_wait(self, process: asyncio.subprocess.Process, signal_number: int, timeout: float) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal_number)
        try:
            await asyncio.wait_for(process.wait(), timeout)
        except TimeoutError:
            pass

    async def _watch_changes(self) -> None:
        assert self.config is not None
        watch_paths = [self.root / "sophie_bot", self.root / "debug", self.root / "locales", self.config.path]
        for local_library in (self.root / "libs" / "ass", self.root / "libs" / "stf"):
            if local_library.exists():
                watch_paths.append(local_library)
        async for changes in awatch(*watch_paths, debounce=500, step=100, watch_filter=self._watch_filter):
            if self.stop_event.is_set():
                return
            parent_changes: set[Path] = set()
            worker_changed = False
            for _change, path in changes:
                resolved = Path(path).resolve()
                if resolved == self.config.path:
                    worker_changed = True
                    continue
                if not resolved.is_relative_to(self.root):
                    continue
                relative = resolved.relative_to(self.root)
                if self._is_parent_path(relative):
                    parent_changes.add(relative)
                if self._is_worker_path(relative):
                    worker_changed = True
            if parent_changes:
                assert self.state is not None
                self.state.restart_required = True
                self.state.detail = _("Debugger parent code changed; restart make dev to load it")
                await self.state.notify_status_change()
                continue
            if worker_changed:
                await self.restart_worker()

    def _watch_filter(self, change: Change, path: str) -> bool:
        assert self.config is not None
        del change
        candidate = Path(path)
        parts = set(candidate.parts)
        if parts & {"__pycache__", "node_modules", "dist", "tests", ".pytest_cache"}:
            return False
        if candidate.resolve() == self.config.path:
            return True
        return candidate.suffix in {".py", ".mo"}

    @staticmethod
    def _is_parent_path(path: Path) -> bool:
        if path in PARENT_DEBUG_PATHS:
            return True
        return path.parts[:1] == ("debug",) and path.suffix == ".py" and not Supervisor._is_worker_path(path)

    @staticmethod
    def _is_worker_path(path: Path) -> bool:
        if path in WORKER_DEBUG_PATHS or path.parts[:2] == ("debug", "adapters"):
            return True
        return path.parts[:1] in {("sophie_bot",), ("locales",), ("libs",)}

    async def _sample_parent_resources(self) -> None:
        assert self.state is not None
        collector_process = psutil.Process()
        vite_process: psutil.Process | None = None
        first = True
        while not self.stop_event.is_set():
            if self.vite is not None and vite_process is None:
                with contextlib.suppress(psutil.Error):
                    vite_process = psutil.Process(self.vite.pid)
            timestamp = time.time()
            self.state.resources["collector"].append(self._process_sample(collector_process, timestamp, first))
            if vite_process is not None:
                with contextlib.suppress(psutil.Error):
                    self.state.resources["vite"].append(self._process_sample(vite_process, timestamp, first))
            first = False
            await asyncio.sleep(1)

    @staticmethod
    def _process_sample(process: psutil.Process, timestamp: float, first: bool) -> dict[str, Any]:
        memory = process.memory_info()
        return {
            "timestamp": timestamp,
            "pid": process.pid,
            "cpu_percent": None if first else process.cpu_percent(None),
            "rss": memory.rss,
            "threads": process.num_threads(),
            "open_fds": process.num_fds() if hasattr(process, "num_fds") else None,
        }

    async def _wait_until_stopped(self) -> None:
        while not self.stop_event.is_set():
            if self.server_task is not None and self.server_task.done():
                self.stop_event.set()
                raise DebugLaunchError("Collector stopped unexpectedly")
            if self.vite is not None and self.vite.returncode is not None:
                self.stop_event.set()
                raise DebugLaunchError(f"Vite stopped unexpectedly with exit code {self.vite.returncode}")
            await asyncio.sleep(0.2)

    async def shutdown(self) -> None:
        self.stop_event.set()
        if self.signal_handlers_installed:
            loop = asyncio.get_running_loop()
            for signal_number in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(signal_number)
            self.signal_handlers_installed = False
        await self._stop_worker()
        if self.state is not None:
            self.state.state = "stopped"
            await self.state.notify_status_change()
        for task in (self.watcher_task, self.resource_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self.watcher_task, self.resource_task) if task is not None),
            return_exceptions=True,
        )
        if self.vite is not None and self.vite.returncode is None:
            await self._signal_and_wait(self.vite, signal.SIGTERM, 2)
            if self.vite.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.vite.pid, signal.SIGKILL)
                await self.vite.wait()
        for task in self.vite_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.vite_tasks, return_exceptions=True)
        if self.server is not None:
            self.server.should_exit = True
        if self.server_task is not None:
            await self.server_task
        if self.collector_socket is not None:
            self.collector_socket.close()
            self.collector_socket = None
        self._remove_session_file()
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None

    def _remove_session_file(self) -> None:
        try:
            content = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if content.get("session_id") == self.session_id:
            with contextlib.suppress(OSError):
                self.session_path.unlink()


async def run_supervisor(
    *,
    config_path: str | Path,
    port: int,
    ui_port: int,
    open_browser: bool,
) -> None:
    supervisor = Supervisor(
        root=Path(__file__).resolve().parent.parent,
        config_path=config_path,
        port=port,
        ui_port=ui_port,
        open_browser=open_browser,
    )
    await supervisor.run()
