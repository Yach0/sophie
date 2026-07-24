from __future__ import annotations

import functools
import inspect
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import count_metric, distribution_metric
from sophie_bot.utils.logger import log

F = TypeVar("F", bound=Callable[..., Any])


def _classify_service(service_name: str) -> tuple[str, str] | None:
    if service_name.startswith("mongo_"):
        return "mongo", service_name.removeprefix("mongo_") or "query"

    if service_name.startswith("redis_"):
        return "redis", service_name.removeprefix("redis_") or "query"

    return None


def _extract_telegram_method(service_name: str) -> str | None:
    if not service_name.startswith("telegram_"):
        return None

    method_name = service_name.removeprefix("telegram_").strip()
    if not method_name:
        return "api_call"

    return method_name


def _record_external_metrics(
    service_name: str,
    duration: float,
    status: str,
    exception_name: str | None = None,
) -> None:
    db_service = _classify_service(service_name)
    telegram_method = _extract_telegram_method(service_name)

    distribution_metric(
        "sophie.external_request.duration",
        duration,
        attributes={"service": service_name},
        unit="second",
    )

    if db_service is not None:
        db_name, operation_name = db_service
        distribution_metric(
            "sophie.db.query.duration",
            duration,
            attributes={"db": db_name, "operation": operation_name, "status": status},
            unit="second",
        )

    if telegram_method is not None:
        distribution_metric(
            "sophie.api.telegram.duration",
            duration,
            attributes={"method": telegram_method, "status": status},
            unit="second",
        )

    if exception_name is None:
        return

    count_metric(
        "sophie.external_errors",
        attributes={"service": service_name, "exception": exception_name},
    )

    if db_service is not None:
        db_name, operation_name = db_service
        count_metric(
            "sophie.db.errors",
            attributes={"db": db_name, "operation": operation_name, "error_type": exception_name},
        )

    if telegram_method is not None:
        count_metric(
            "sophie.api.telegram.errors",
            attributes={"method": telegram_method, "error_type": exception_name},
        )


@asynccontextmanager
async def time_external_service(service_name: str) -> AsyncGenerator[None]:
    """Context manager for timing external service calls"""
    if not CONFIG.metrics_enable:
        yield
        return

    start_time = time.perf_counter()
    exception_name: str | None = None
    status = "ok"

    try:
        yield
    except Exception as error:
        exception_name = type(error).__name__
        status = "error"
        raise
    finally:
        duration = time.perf_counter() - start_time
        _record_external_metrics(
            service_name=service_name,
            duration=duration,
            status=status,
            exception_name=exception_name,
        )
        if exception_name is not None:
            log.debug(
                "External service error tracked", service=service_name, exception_type=exception_name, duration=duration
            )


def instrument_external_service(service_name: str):
    """Decorator for instrumenting external service calls"""

    def decorator(func: F) -> F:
        if not CONFIG.metrics_enable:
            return func

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            async with time_external_service(service_name):
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not CONFIG.metrics_enable:
                return func(*args, **kwargs)

            start_time = time.perf_counter()
            exception_name: str | None = None
            status = "ok"

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as error:
                exception_name = type(error).__name__
                status = "error"
                raise
            finally:
                duration = time.perf_counter() - start_time

                _record_external_metrics(
                    service_name=service_name,
                    duration=duration,
                    status=status,
                    exception_name=exception_name,
                )

        if inspect.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator


# Specific service instrumentations


def time_mongo_operation(operation_name: str = "query"):
    """Context manager specifically for MongoDB operations"""
    return time_external_service(f"mongo_{operation_name}")


def time_redis_operation(operation_name: str = "query"):
    """Context manager specifically for Redis operations"""
    return time_external_service(f"redis_{operation_name}")


def time_openai_operation(operation_name: str = "api_call"):
    """Context manager specifically for OpenAI operations"""
    return time_external_service(f"openai_{operation_name}")


def time_telegram_api_operation(operation_name: str = "api_call"):
    """Context manager specifically for Telegram API operations"""
    return time_external_service(f"telegram_{operation_name}")


# Convenience decorators for specific services


def instrument_mongo(operation_name: str = "query"):
    """Decorator for MongoDB operations"""
    return instrument_external_service(f"mongo_{operation_name}")


def instrument_redis(operation_name: str = "query"):
    """Decorator for Redis operations"""
    return instrument_external_service(f"redis_{operation_name}")


def instrument_openai(operation_name: str = "api_call"):
    """Decorator for OpenAI API calls"""
    return instrument_external_service(f"openai_{operation_name}")


def instrument_telegram_api(operation_name: str = "api_call"):
    """Decorator for Telegram API calls"""
    return instrument_external_service(f"telegram_{operation_name}")


# Helper functions for manual instrumentation


class ExternalServiceTracker:
    """Helper class for manual external service tracking"""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self.start_time: float | None = None

    def start(self) -> None:
        """Start timing an external service call"""
        self.start_time = time.perf_counter()

    def finish(self, exception: Exception | None = None) -> None:
        """Finish timing an external service call"""
        if not CONFIG.metrics_enable or self.start_time is None:
            return

        duration = time.perf_counter() - self.start_time
        status = "error" if exception else "ok"
        exception_name = type(exception).__name__ if exception else None

        _record_external_metrics(
            service_name=self.service_name,
            duration=duration,
            status=status,
            exception_name=exception_name,
        )
        if exception_name is not None:
            log.debug(
                "External service error tracked manually",
                service=self.service_name,
                exception_type=exception_name,
                duration=duration,
            )


def create_service_tracker(service_name: str) -> ExternalServiceTracker:
    """Create a manual service tracker"""
    return ExternalServiceTracker(service_name)
