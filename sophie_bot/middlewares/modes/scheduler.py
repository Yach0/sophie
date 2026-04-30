from __future__ import annotations

import asyncio

from aiogram import Dispatcher

from sophie_bot.config import CONFIG
from sophie_bot.startup import start_init


async def _scheduler_main() -> None:
    """Initialize and run the scheduler on the main event loop."""
    from sophie_bot.services.scheduler import scheduler

    await start_init(dp=Dispatcher())
    scheduler.start()

    # Block until interrupted
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=False)


def start_scheduler_mode() -> None:
    if CONFIG.dev_reload:
        from sophie_bot.utils.dev_runner import run_with_reload

        run_with_reload("scheduler")
        return

    try:
        asyncio.run(_scheduler_main())
    except (KeyboardInterrupt, SystemExit):
        pass
