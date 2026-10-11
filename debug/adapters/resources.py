from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import psutil

from debug.adapters import EventRecorder
from debug.i18n import gettext_debug as _
from debug.protocol import Category, Phase

_SAMPLE_INTERVAL = 1.0


async def sample_worker_resources(recorder: EventRecorder) -> None:
    process = psutil.Process(os.getpid())
    process.cpu_percent(None)
    first = True
    loop = asyncio.get_running_loop()
    target = loop.time() + _SAMPLE_INTERVAL
    while True:
        await asyncio.sleep(max(0.0, target - loop.time()))
        observed = loop.time()
        lag_ms = max(0.0, observed - target) * 1_000
        target = observed + _SAMPLE_INTERVAL
        try:
            memory = process.memory_info()
            cpu_percent = process.cpu_percent(None)
            sample: dict[str, Any] = {
                "timestamp": time.time(),
                "pid": process.pid,
                "cpu_percent": None if first else cpu_percent,
                "rss": memory.rss,
                "threads": process.num_threads(),
                "open_fds": process.num_fds() if hasattr(process, "num_fds") else None,
                "event_loop_lag_ms": lag_ms,
                "active_tasks": sum(1 for task in asyncio.all_tasks(loop) if not task.done()),
            }
            first = False
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            recorder.sink.recorder_errors += 1
            continue
        recorder.emit(
            category=Category.RESOURCE,
            name="worker.sample",
            phase=Phase.INSTANT,
            summary=_("Worker resource sample"),
            payload=sample,
        )
