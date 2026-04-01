from __future__ import annotations

import asyncio
import time

from sophie_bot.services.sentry_metrics import set_gauge_metric
from sophie_bot.utils.logger import log


async def start_background_tasks() -> None:
    """Start background tasks for metrics collection"""

    # Start event loop lag monitoring
    asyncio.create_task(_monitor_event_loop_lag())

    log.info("Started metrics background tasks")


async def _monitor_event_loop_lag() -> None:
    """Monitor event loop lag and update gauge"""
    while True:
        try:
            # Measure event loop lag
            expected_sleep = 0.5
            start_time = time.perf_counter()

            await asyncio.sleep(expected_sleep)

            end_time = time.perf_counter()
            actual_sleep = end_time - start_time
            lag = max(0, actual_sleep - expected_sleep)

            set_gauge_metric("sophie.event_loop.lag", lag, unit="second")

            # Log excessive lag
            if lag > 1.0:
                log.warning("High event loop lag detected", lag_seconds=lag)
            elif lag > 0.1:
                log.debug("Event loop lag detected", lag_seconds=lag)

        except Exception as e:
            log.error("Error in event loop lag monitoring", error=str(e))
            # Continue monitoring even if there's an error
            await asyncio.sleep(1.0)
