from __future__ import annotations

import asyncio
from datetime import datetime

from sophie_bot.config import CONFIG
from sophie_bot.runtime import build_scheduler_runtime
from sophie_bot.services.health import HEARTBEAT_INTERVAL_SECONDS, write_heartbeat
from sophie_bot.startup import initialize_scheduler_mode


async def _scheduler_main() -> None:
    """Initialize and run the scheduler on the main event loop."""
    runtime = build_scheduler_runtime()

    await initialize_scheduler_mode(runtime)

    runtime.scheduler.add_job(
        write_heartbeat,
        "interval",
        seconds=HEARTBEAT_INTERVAL_SECONDS,
        args=[CONFIG.mode],
        jobstore="ram",
        next_run_time=datetime.now(),
    )
    runtime.scheduler.start()

    # Block until interrupted
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        runtime.scheduler.shutdown(wait=False)


def start_scheduler_mode() -> None:
    if CONFIG.dev_reload:
        from sophie_bot.utils.dev_runner import run_with_reload

        run_with_reload("scheduler")
        return

    try:
        asyncio.run(_scheduler_main())
    except (KeyboardInterrupt, SystemExit):
        pass
