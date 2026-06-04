from __future__ import annotations

import asyncio
import time

import psutil

from sophie_bot.services.sentry_metrics import set_gauge_metric
from sophie_bot.utils.logger import log


async def start_background_tasks() -> None:
    """Start background tasks for metrics collection"""

    # Start event loop lag monitoring
    asyncio.create_task(_monitor_event_loop_lag())

    # Start system metrics collection
    asyncio.create_task(_collect_system_metrics())

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


async def _collect_system_metrics() -> None:
    """Collect system metrics (CPU, load, memory, swap) and send to Sentry"""
    while True:
        _report_cpu_metrics()
        _report_load_metrics()
        _report_memory_metrics()
        _report_swap_metrics()

        await asyncio.sleep(10)


def _report_cpu_metrics() -> None:
    """Report CPU usage percentage"""
    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        set_gauge_metric("sophie.system.cpu_percent", cpu_percent, unit="percent")
    except Exception as e:
        log.warning("Failed to collect CPU metrics", error=str(e))


def _report_load_metrics() -> None:
    """Report 1/5/15 minute load averages"""
    try:
        load_avg = psutil.getloadavg()
        set_gauge_metric("sophie.system.load_avg.1m", load_avg[0])
        set_gauge_metric("sophie.system.load_avg.5m", load_avg[1])
        set_gauge_metric("sophie.system.load_avg.15m", load_avg[2])
    except Exception as e:
        log.warning("Failed to collect load average metrics", error=str(e))


def _report_memory_metrics() -> None:
    """Report virtual memory usage"""
    try:
        memory = psutil.virtual_memory()
        set_gauge_metric("sophie.system.memory.total", memory.total, unit="byte")
        set_gauge_metric("sophie.system.memory.available", memory.available, unit="byte")
        set_gauge_metric("sophie.system.memory.used", memory.used, unit="byte")
        set_gauge_metric("sophie.system.memory.percent", memory.percent, unit="percent")
    except Exception as e:
        log.warning("Failed to collect memory metrics", error=str(e))


def _report_swap_metrics() -> None:
    """Report swap memory usage"""
    try:
        swap = psutil.swap_memory()
        set_gauge_metric("sophie.system.swap.total", swap.total, unit="byte")
        set_gauge_metric("sophie.system.swap.used", swap.used, unit="byte")
        set_gauge_metric("sophie.system.swap.percent", swap.percent, unit="percent")
    except Exception as e:
        log.warning("Failed to collect swap metrics", error=str(e))
