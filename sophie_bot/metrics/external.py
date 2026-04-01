from __future__ import annotations

import asyncio
import functools
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Optional, TypeVar

from sophie_bot.config import CONFIG
from sophie_bot.services.sentry_metrics import count_metric, distribution_metric
from sophie_bot.utils.logger import log

# Global metrics instance - will be set during initialization
_metrics = None

F = TypeVar("F", bound=Callable[..., Any])


def set_metrics(metrics) -> None:
    """Set the global metrics instance for external service instrumentation"""
    global _metrics
    _metrics = metrics
    log.info("External service metrics instrumentation enabled")


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


@asynccontextmanager
async def time_external_service(service_name: str) -> AsyncGenerator[None, None]:
    """Context manager for timing external service calls"""
    if not _metrics or not CONFIG.metrics_enable:
        yield
        return

    start_time = time.perf_counter()
    exception_occurred = False
    exception_name = "unknown"
    status = "ok"

    try:
        yield
    except Exception as e:
        exception_occurred = True
        exception_name = type(e).__name__
        status = "error"
        raise
    finally:
        duration = time.perf_counter() - start_time
        db_service = _classify_service(service_name)
        telegram_method = _extract_telegram_method(service_name)

        # Record duration
        _metrics.external_request_duration_seconds.labels(service=service_name).observe(duration)
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

        # Record error if there was an exception
        if exception_occurred:
            _metrics.external_errors_total.labels(service=service_name, exception=exception_name).inc()
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
            # For sync functions, we still need to track them
            if not _metrics:
                return func(*args, **kwargs)

            start_time = time.perf_counter()
            exception_occurred = False
            exception_name = "unknown"

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                exception_occurred = True
                exception_name = type(e).__name__
                raise
            finally:
                duration = time.perf_counter() - start_time

                # Record duration
                _metrics.external_request_duration_seconds.labels(service=service_name).observe(duration)
                distribution_metric(
                    "sophie.external_request.duration",
                    duration,
                    attributes={"service": service_name},
                    unit="second",
                )

                # Record error if there was an exception
                if exception_occurred:
                    _metrics.external_errors_total.labels(service=service_name, exception=exception_name).inc()
                    count_metric(
                        "sophie.external_errors",
                        attributes={"service": service_name, "exception": exception_name},
                    )

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        else:
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

    def __init__(self, service_name: str):
        self.service_name = service_name
        self.start_time: Optional[float] = None

    def start(self) -> None:
        """Start timing an external service call"""
        self.start_time = time.perf_counter()

    def finish(self, exception: Optional[Exception] = None) -> None:
        """Finish timing an external service call"""
        if not _metrics or not CONFIG.metrics_enable or self.start_time is None:
            return

        duration = time.perf_counter() - self.start_time
        db_service = _classify_service(self.service_name)
        telegram_method = _extract_telegram_method(self.service_name)
        status = "error" if exception else "ok"

        # Record duration
        _metrics.external_request_duration_seconds.labels(service=self.service_name).observe(duration)
        distribution_metric(
            "sophie.external_request.duration",
            duration,
            attributes={"service": self.service_name},
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

        # Record error if there was an exception
        if exception:
            exception_name = type(exception).__name__
            _metrics.external_errors_total.labels(service=self.service_name, exception=exception_name).inc()
            count_metric(
                "sophie.external_errors",
                attributes={"service": self.service_name, "exception": exception_name},
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

            log.debug(
                "External service error tracked manually",
                service=self.service_name,
                exception_type=exception_name,
                duration=duration,
            )


def create_service_tracker(service_name: str) -> ExternalServiceTracker:
    """Create a manual service tracker"""
    return ExternalServiceTracker(service_name)


# Integration helpers for existing code


def patch_beanie_operations():
    """Patch Beanie operations to add metrics (advanced usage)"""
    if not _metrics or not CONFIG.metrics_enable:
        return

    try:
        # This is an advanced feature that could be implemented later
        # to automatically instrument all Beanie operations
        log.debug("Beanie operation patching not implemented yet")
    except ImportError:
        log.debug("Beanie not available for patching")


def patch_redis_operations():
    """Patch Redis operations to add metrics (advanced usage)"""
    if not _metrics or not CONFIG.metrics_enable:
        return

    try:
        # This is an advanced feature that could be implemented later
        # to automatically instrument all Redis operations
        log.debug("Redis operation patching not implemented yet")
    except ImportError:
        log.debug("Redis not available for patching")


# Example usage patterns:
#
# # Using decorators:
# @instrument_mongo("find")
# async def find_user(user_id: int):
#     return await UserModel.find_one(UserModel.user_id == user_id)
#
# @instrument_redis("get")
# async def get_cached_value(key: str):
#     return await aredis.get(key)
#
# @instrument_openai("completion")
# async def get_completion(prompt: str):
#     return await openai_client.chat.completions.create(...)
#
# # Using context managers:
# async with time_mongo_operation("aggregate"):
#     results = await SomeModel.aggregate([...]).to_list()
#
# # Using manual tracking:
# tracker = create_service_tracker("custom_api")
# tracker.start()
# try:
#     result = await some_external_api_call()
# except Exception as e:
#     tracker.finish(e)
#     raise
# else:
#     tracker.finish()
