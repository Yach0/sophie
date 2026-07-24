from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Final, override

import sentry_sdk
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from sophie_bot.metrics.update_info import extract_command_name, extract_update_info
from sophie_bot.utils.feature_flags import FeatureType, is_enabled

# Feature flag gating per-update Sentry transactions (runtime kill switch).
TRACING_FLAG: Final[FeatureType] = "sentry_update_tracing"


class SentryTracingMiddleware(BaseMiddleware):
    """Open one Sentry transaction per Telegram update.

    Sophie enables the Redis, PyMongo, and aiohttp Sentry integrations, but without a
    root transaction their spans have nothing to attach to and ``profile_lifecycle="trace"``
    never fires. Wrapping the update dispatch here gives every inner middleware, DB/cache
    call, and handler a nested span, producing real performance traces and profiles.

    Registered as the first inner ``update`` middleware, so it runs after the media-group
    aggregator: that keeps album-collection idle time out of the transaction duration.

    Effective sampling is still governed by ``sentry_traces_sample_rate`` — when that is
    unset, ``start_transaction`` returns a cheap unsampled transaction, so the flag being
    on does not force traces to be recorded.
    """

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not await is_enabled(TRACING_FLAG):
            return await handler(event, data)

        update_info = extract_update_info(event)
        command_name = extract_command_name(event)

        transaction_name = f"command:{command_name}" if command_name else f"update:{update_info['update_type']}"

        with sentry_sdk.start_transaction(op="bot.update", name=transaction_name) as transaction:
            transaction.set_tag("update_type", update_info["update_type"])
            transaction.set_tag("chat_type", update_info["chat_type"])
            transaction.set_tag("transport", update_info["transport"])
            if update_info["message_kind"]:
                transaction.set_tag("message_kind", update_info["message_kind"])
            if command_name:
                transaction.set_tag("command", command_name)

            return await handler(event, data)
