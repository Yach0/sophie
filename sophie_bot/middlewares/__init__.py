from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.middleware import FSMContextMiddleware
from aiogram.utils.i18n import ConstI18nMiddleware
from ass_tg.middleware import ArgsMiddleware

from sophie_bot.config import CONFIG
from sophie_bot.middlewares.admincache import AdmincacheMiddleware
from sophie_bot.middlewares.beta import BetaMiddleware
from sophie_bot.middlewares.connections import ConnectionsMiddleware
from sophie_bot.middlewares.disabling import DisablingMiddleware
from sophie_bot.middlewares.localization import LocalizationMiddleware
from sophie_bot.middlewares.logic import OrMiddleware
from sophie_bot.middlewares.media_group import (
    MediaGroupAggregatorMiddleware,
    RedisMediaGroupAggregator,
)
from sophie_bot.middlewares.memory_debug import TracemallocMiddleware
from sophie_bot.middlewares.save_chats import SaveChatsMiddleware
from sophie_bot.middlewares.sentry_tracing import SentryTracingMiddleware
from sophie_bot.middlewares.spam_detection import SpamDetectionMiddleware
from sophie_bot.services.bot import get_bot_runtime, redis
from sophie_bot.services.i18n import i18n
from sophie_bot.utils.logger import log

# Global metrics instance - will be set during initialization
_metrics_middleware = None

localization_middleware = LocalizationMiddleware(i18n)
try_localization_middleware = OrMiddleware(localization_middleware, ConstI18nMiddleware("en_US", i18n))


def set_metrics_middleware(middleware) -> None:
    """Set the metrics middleware instance"""
    global _metrics_middleware
    _metrics_middleware = middleware
    log.info("Metrics middleware set")


def enable_middlewares(dispatcher: Dispatcher | None = None) -> None:
    active_dispatcher = dispatcher or get_bot_runtime().dispatcher

    if CONFIG.debug_mode in ("normal", "high"):
        from .debug import EventSeparatorMiddleware

        active_dispatcher.update.outer_middleware(EventSeparatorMiddleware())

    if CONFIG.debug_mode == "high":
        from .debug import UpdateDebugMiddleware

        active_dispatcher.update.middleware(UpdateDebugMiddleware())

    # Media-group aggregator must run before the FSM middleware: that middleware holds a
    # per-(chat, user, thread) isolation lock, and all items of one album share that key.
    # Running inside the lock would deadlock the aggregator's delay-loop (later album items
    # could never join the buffered group). Insert it right before FSMContextMiddleware.
    outer_middlewares = active_dispatcher.update.outer_middleware._middlewares
    fsm_index = next(
        (index for index, middleware in enumerate(outer_middlewares) if isinstance(middleware, FSMContextMiddleware)),
        len(outer_middlewares),
    )
    outer_middlewares.insert(
        fsm_index,
        MediaGroupAggregatorMiddleware(RedisMediaGroupAggregator(redis)),
    )

    # Register outermost among inner middlewares (before localization) so the Sentry
    # transaction wraps all per-update work + handler, but runs after the media-group
    # aggregator to keep album-collection idle time out of the transaction duration.
    if CONFIG.sentry_url:
        active_dispatcher.update.middleware(SentryTracingMiddleware())

    active_dispatcher.update.middleware(localization_middleware)

    # Register metrics middleware if enabled
    if CONFIG.metrics_enable and _metrics_middleware:
        active_dispatcher.update.middleware(_metrics_middleware)
        log.info("Metrics middleware registered")

    if CONFIG.proxy_enable:
        log.info("Enabled Proxy!")
        active_dispatcher.update.middleware(BetaMiddleware())

    active_dispatcher.message.middleware(ArgsMiddleware(i18n=i18n))

    active_dispatcher.update.outer_middleware(SaveChatsMiddleware())
    active_dispatcher.update.middleware(AdmincacheMiddleware())
    active_dispatcher.update.middleware(SpamDetectionMiddleware())

    active_dispatcher.update.middleware(ConnectionsMiddleware())
    active_dispatcher.message.middleware(DisablingMiddleware())

    if CONFIG.debug_mode == "high":
        from .debug import DataDebugMiddleware, HandlerDebugMiddleware

        active_dispatcher.update.middleware(DataDebugMiddleware())
        active_dispatcher.update.middleware(HandlerDebugMiddleware())

    if CONFIG.memory_debug:
        active_dispatcher.update.middleware(TracemallocMiddleware())
