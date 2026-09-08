from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import partial

from sophie_bot.config import CONFIG
from sophie_bot.modules import track_scheduler_callback
from sophie_bot.runtime import build_scheduler_runtime
from sophie_bot.services.health import HEARTBEAT_INTERVAL_SECONDS, write_heartbeat_guarded
from sophie_bot.startup import initialize_scheduler_mode


async def _scheduler_main() -> None:
    async with build_scheduler_runtime() as runtime:
        await initialize_scheduler_mode(runtime)
        runtime.scheduler.add_job(
            track_scheduler_callback(
                partial(
                    write_heartbeat_guarded,
                    CONFIG.mode,
                    redis=runtime.services.redis,
                ),
                runtime.services,
            ),
            "interval",
            seconds=HEARTBEAT_INTERVAL_SECONDS,
            jobstore="ram",
            next_run_time=datetime.now(UTC),
        )
        runtime.scheduler.start()
        try:
            await asyncio.Event().wait()
        finally:
            runtime.scheduler.shutdown(wait=True)
            await asyncio.sleep(0)


def start_scheduler_mode() -> None:
    if CONFIG.dev_reload:
        from sophie_bot.utils.dev_runner import run_with_reload

        run_with_reload("scheduler")
        return
    try:
        asyncio.run(_scheduler_main())
    except (KeyboardInterrupt, SystemExit):
        pass
