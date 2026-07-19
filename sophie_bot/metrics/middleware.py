from __future__ import annotations

import random
import time
from typing import Any, Awaitable, Callable, Dict, cast

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from sophie_bot.metrics.update_info import extract_command_name, extract_update_info
from sophie_bot.services.sentry_metrics import change_gauge_metric, count_metric, distribution_metric
from sophie_bot.utils.logger import log


class MetricsMiddleware(BaseMiddleware):
    """Aiogram middleware for collecting Sentry metrics."""

    def __init__(self, config: Any) -> None:
        self.config = config
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process update and collect metrics"""

        # Skip sampling if configured
        if self.config.metrics_sample_ratio < 1.0 and random.random() > self.config.metrics_sample_ratio:
            return await handler(event, data)

        # Extract update information
        update_info = extract_update_info(event)
        command_name = extract_command_name(event)

        # Increment update counter
        count_metric(
            "sophie.updates",
            attributes={
                "update_type": update_info["update_type"],
                "chat_type": update_info["chat_type"],
                "transport": update_info["transport"],
            },
        )

        # Increment message counter if it's a message
        if update_info["message_kind"]:
            count_metric("sophie.messages", attributes={"message_kind": update_info["message_kind"]})

        # Track inflight handlers
        change_gauge_metric("sophie.inflight_handlers", 1)

        # Measure handler duration
        start_time = time.perf_counter()
        handler_name = self._get_handler_name(handler, event)
        command_status = "ok"

        try:
            result = await handler(event, data)
            return result

        except Exception as e:
            command_status = "error"
            # Track handler errors
            exception_name = type(e).__name__
            count_metric(
                "sophie.handler_errors",
                attributes={"handler": handler_name, "exception": exception_name},
            )

            log.debug("Handler error tracked", handler=handler_name, exception_type=exception_name, error=str(e))

            # Re-raise the exception
            raise

        finally:
            # Always decrement inflight handlers and record duration
            change_gauge_metric("sophie.inflight_handlers", -1)

            # Record handler duration
            duration = time.perf_counter() - start_time
            distribution_metric(
                "sophie.handler.duration",
                duration,
                attributes={"handler": handler_name},
                unit="second",
            )

            if command_name is not None:
                count_metric(
                    "sophie.commands.executed",
                    attributes={
                        "command": command_name,
                        "chat_type": update_info["chat_type"],
                        "status": command_status,
                    },
                )
                distribution_metric(
                    "sophie.commands.duration",
                    duration,
                    attributes={"command": command_name, "status": command_status},
                    unit="second",
                )

    def _get_handler_name(self, handler: Callable, event: TelegramObject | None) -> str:
        """Extract handler name for labeling"""
        handler_name: str = "unknown"

        # Handle functools.partial objects (common with aiogram class-based handlers)
        if hasattr(handler, "func"):
            # This is likely a functools.partial object
            actual_func = handler.func
            if hasattr(actual_func, "__self__") and hasattr(actual_func, "__class__"):
                # This is a bound method, get the class name
                handler_name = actual_func.__self__.__class__.__name__
            elif hasattr(actual_func, "__name__"):
                handler_name = cast(str, actual_func.__name__)
            else:
                handler_name = str(actual_func)
        # Handle bound methods directly
        elif hasattr(handler, "__self__"):
            self_obj = getattr(handler, "__self__", None)
            if self_obj is not None and hasattr(self_obj, "__class__"):
                handler_name = self_obj.__class__.__name__
        # Handle regular functions
        elif hasattr(handler, "__name__"):
            handler_name = cast(str, handler.__name__)
        # Check for problematic string representations first (before class check)
        elif callable(handler):
            handler_str = str(handler)
            # Try to extract class name from string representation
            if "bound method" in handler_str and "of <" in handler_str:
                # Example: "<bound method AiPmStop.handle of <...>>"
                parts = handler_str.split(".")
                if len(parts) >= 2:
                    class_part = parts[-2].split()[-1]  # Get the class name
                    handler_name = class_part
            elif " object at " in handler_str:
                # Handle cases like "<sophie_bot.middlewares.connections.ConnectionsMiddleware object at 0x...>"
                before_object = handler_str.split(" object at ")[0]
                handler_name = before_object.split(".")[-1] if "." in before_object else before_object.strip("<>")
            elif " " in handler_str:
                handler_name = handler_str.split(" ")[1]
            # Handle classes (if handler is a class itself) - moved after string parsing
            elif hasattr(handler, "__class__") and handler.__class__.__name__ != "function":
                handler_name = handler.__class__.__name__
            else:
                handler_name = handler_str

        # Clean up handler name to avoid high cardinality
        if "." in handler_name:
            handler_name = handler_name.split(".")[-1]
        if "<" in handler_name:
            handler_name = handler_name.replace("<", "").replace(">", "")
        if "'" in handler_name:
            handler_name = handler_name.replace("'", "")

        return handler_name[:50]  # Limit length to avoid cardinality issues
