from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, override

import sentry_sdk
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from sophie_bot.metrics.update_info import extract_command_name, extract_update_info


class SentryTracingMiddleware(BaseMiddleware):
    """Open one Sentry trace segment per Telegram update.

    Sophie enables the Redis, PyMongo, and aiohttp Sentry integrations, but without a
    root span the update has no unified trace and ``profile_lifecycle="trace"``
    never fires. Wrapping the update dispatch here gives every inner middleware, DB/cache
    call, and handler a nested span, producing real performance traces and profiles.

    Registered as the first inner ``update`` middleware, so it runs after the media-group
    aggregator: that keeps album-collection idle time out of the span duration.

    Effective sampling is governed by ``sentry_traces_sample_rate`` (default 1.0
    when a Sentry DSN is set). Lower it to reduce volume, or set it to zero to
    disable traces without disabling error reporting.
    """

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_info = extract_update_info(event)
        command_name = extract_command_name(event)

        span_name = f"command:{command_name}" if command_name else f"update:{update_info['update_type']}"
        attributes = {
            "sentry.op": "bot.update",
            "update_type": update_info["update_type"],
            "chat_type": update_info["chat_type"],
            "transport": update_info["transport"],
        }
        if message_kind := update_info["message_kind"]:
            attributes["message_kind"] = message_kind
        if command_name:
            attributes["command"] = command_name

        with sentry_sdk.start_span(name=span_name, attributes=attributes, parent_span=None):
            return await handler(event, data)
