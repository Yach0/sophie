from __future__ import annotations

import asyncio

from sophie_bot.config import CONFIG
from sophie_bot.runtime import build_scheduler_runtime
from sophie_bot.startup import initialize_scheduler_mode


async def _scheduler_main() -> None:
    """Initialize and run the scheduler on the main event loop."""
    runtime = build_scheduler_runtime()

    await initialize_scheduler_mode(runtime)
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
