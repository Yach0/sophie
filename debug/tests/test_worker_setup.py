from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from debug import supervisor as supervisor_module
from debug.collector import CollectorState
from debug.supervisor import DebugConfig, Supervisor


def make_supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Supervisor:
    config = DebugConfig(
        path=tmp_path / "debug.env",
        values={
            "ENVIRONMENT": "development",
            "TOKEN": "999999:development-token",
            "MONGO_HOST": "127.0.0.1",
            "MONGO_PORT": "27017",
            "MONGO_DB": "debug",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "6379",
            "REDIS_DB_STATES": "15",
            "REDIS_DB_FSM": "14",
            "REDIS_DB_SCHEDULE": "13",
            "RUN_MIGRATIONS_ON_STARTUP": "false",
        },
    )
    monkeypatch.setattr(supervisor_module, "preflight_config", lambda _path: config)
    supervisor = Supervisor(root=tmp_path, config_path=config.path, port=8079, ui_port=5174, open_browser=False)
    supervisor.config = config
    supervisor.state = CollectorState(
        session_id="session",
        bearer_token="bearer",
        browser_credential="browser",
        csrf_token="csrf",
        api_origin=supervisor.api_origin,
        ui_origin=supervisor.ui_origin,
        sanitized_targets={},
    )
    return supervisor


@pytest.mark.parametrize("stage", ["telemetry", "control", "channel"])
@pytest.mark.parametrize("outcome", ["failure", "cancellation", "repeated_cancellation"])
def test_failed_worker_setup_reaps_process_and_closes_every_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, outcome: str
) -> None:
    async def scenario() -> None:
        supervisor = make_supervisor(tmp_path, monkeypatch)
        assert supervisor.state is not None
        created_sockets: list[socket.socket] = []
        processes: list[asyncio.subprocess.Process] = []
        stage_reached = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        real_socketpair = socket.socketpair
        real_spawn = asyncio.create_subprocess_exec
        real_connect = asyncio.open_connection
        real_set_channel = supervisor.state.set_worker_channel
        real_stop_process = supervisor._stop_worker_process
        connections = 0

        def socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
            pair = real_socketpair(*args, **kwargs)
            created_sockets.extend(pair)
            return pair

        async def spawn(*_args: Any, **_kwargs: Any) -> asyncio.subprocess.Process:
            process = await real_spawn(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            processes.append(process)
            return process

        async def interrupt() -> None:
            stage_reached.set()
            if outcome == "failure":
                raise OSError("injected setup failure")
            await asyncio.Event().wait()

        async def connect(*, sock: socket.socket, limit: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            nonlocal connections
            connections += 1
            if stage == ("telemetry" if connections == 1 else "control"):
                await interrupt()
            return await real_connect(sock=sock, limit=limit)

        async def set_channel(_state: CollectorState, run_id: str, worker_pid: int | None) -> int:
            generation = await real_set_channel(run_id, worker_pid)
            if stage == "channel":
                await interrupt()
            return generation

        async def stop_process(process: asyncio.subprocess.Process) -> None:
            cleanup_started.set()
            if outcome == "repeated_cancellation":
                await release_cleanup.wait()
            await real_stop_process(process)

        monkeypatch.setattr(socket, "socketpair", socketpair)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(asyncio, "open_connection", connect)
        monkeypatch.setattr(CollectorState, "set_worker_channel", set_channel)
        monkeypatch.setattr(supervisor, "_stop_worker_process", stop_process)
        setup = asyncio.create_task(supervisor._start_worker())
        try:
            await asyncio.wait_for(stage_reached.wait(), 5)
            if outcome != "failure":
                setup.cancel()
            if outcome == "repeated_cancellation":
                await asyncio.wait_for(cleanup_started.wait(), 5)
                setup.cancel()
                release_cleanup.set()
            expected_error = OSError if outcome == "failure" else asyncio.CancelledError
            with pytest.raises(expected_error):
                await asyncio.wait_for(setup, 5)
            assert supervisor.worker is None
            assert supervisor.state.worker_pid is None
            assert len(processes) == 1
            assert processes[0].returncode is not None
            assert len(created_sockets) == 4
            assert all(created_socket.fileno() == -1 for created_socket in created_sockets)
        finally:
            release_cleanup.set()
            if not setup.done():
                setup.cancel()
                with contextlib.suppress(asyncio.CancelledError, OSError):
                    await setup
            for process in processes:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            for created_socket in created_sockets:
                created_socket.close()

    asyncio.run(scenario())


def test_failure_creating_control_socket_pair_closes_telemetry_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        supervisor = make_supervisor(tmp_path, monkeypatch)
        real_socketpair = socket.socketpair
        created_sockets: list[socket.socket] = []

        def socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
            if created_sockets:
                raise OSError("injected socket pair failure")
            pair = real_socketpair(*args, **kwargs)
            created_sockets.extend(pair)
            return pair

        monkeypatch.setattr(socket, "socketpair", socketpair)
        try:
            with pytest.raises(OSError, match="injected socket pair failure"):
                await supervisor._start_worker()
            assert supervisor.worker is None
            assert all(created_socket.fileno() == -1 for created_socket in created_sockets)
        finally:
            for created_socket in created_sockets:
                created_socket.close()

    asyncio.run(scenario())


def test_cancelled_startup_status_closes_both_socket_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        supervisor = make_supervisor(tmp_path, monkeypatch)
        real_socketpair = socket.socketpair
        created_sockets: list[socket.socket] = []
        status_started = asyncio.Event()

        def socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
            pair = real_socketpair(*args, **kwargs)
            created_sockets.extend(pair)
            return pair

        async def notify_status(_state: CollectorState) -> None:
            status_started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(socket, "socketpair", socketpair)
        monkeypatch.setattr(CollectorState, "notify_status_change", notify_status)
        setup = asyncio.create_task(supervisor._start_worker())
        try:
            await asyncio.wait_for(status_started.wait(), 5)
            setup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await setup
            assert supervisor.worker is None
            assert len(created_sockets) == 4
            assert all(created_socket.fileno() == -1 for created_socket in created_sockets)
        finally:
            if not setup.done():
                setup.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await setup
            for created_socket in created_sockets:
                created_socket.close()

    asyncio.run(scenario())
