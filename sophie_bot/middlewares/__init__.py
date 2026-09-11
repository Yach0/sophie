from __future__ import annotations

from typing import Any

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
from sophie_bot.middlewares.media_group import MediaGroupAggregatorMiddleware, RedisMediaGroupAggregator
from sophie_bot.middlewares.memory_debug import TracemallocMiddleware
from sophie_bot.middlewares.save_chats import SaveChatsMiddleware
from sophie_bot.middlewares.sentry_tracing import SentryTracingMiddleware
from sophie_bot.middlewares.spam_detection import SpamDetectionMiddleware
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.logger import log


def create_try_localization_middleware(services: ApplicationServices) -> OrMiddleware:
    localization_middleware = LocalizationMiddleware(services.locales)
    return OrMiddleware(
        localization_middleware,
        ConstI18nMiddleware(services.locales.default_locale, services.locales.i18n),
    )


def enable_middlewares(
    dispatcher: Dispatcher,
    services: ApplicationServices,
    metrics_middleware: Any | None = None,
) -> None:
    if CONFIG.debug_mode in ("normal", "high"):
        from .debug import EventSeparatorMiddleware

        dispatcher.update.outer_middleware(EventSeparatorMiddleware())

    if CONFIG.debug_mode == "high":
        from .debug import UpdateDebugMiddleware

        dispatcher.update.middleware(UpdateDebugMiddleware())

    # Album aggregation must precede FSM isolation or sibling album updates deadlock.
    outer_middlewares = dispatcher.update.outer_middleware._middlewares
    fsm_index = next(
        index for index, middleware in enumerate(outer_middlewares) if isinstance(middleware, FSMContextMiddleware)
    )
    outer_middlewares.insert(
        fsm_index,
        MediaGroupAggregatorMiddleware(RedisMediaGroupAggregator(services.redis)),
    )

    if CONFIG.sentry_url:
        dispatcher.update.middleware(SentryTracingMiddleware())

    dispatcher.update.middleware(LocalizationMiddleware(services.locales))

    if CONFIG.metrics_enable and metrics_middleware is not None:
        dispatcher.update.middleware(metrics_middleware)
        log.info("Metrics middleware registered")

    if CONFIG.proxy_enable:
        log.info("Enabled Proxy!")
        dispatcher.update.middleware(BetaMiddleware())

    dispatcher.message.middleware(ArgsMiddleware(i18n=services.locales.i18n))
    dispatcher.update.outer_middleware(SaveChatsMiddleware())
    dispatcher.update.middleware(AdmincacheMiddleware())
    dispatcher.update.middleware(SpamDetectionMiddleware())
    dispatcher.update.middleware(ConnectionsMiddleware())
    dispatcher.message.middleware(DisablingMiddleware())

    if CONFIG.debug_mode == "high":
        from .debug import DataDebugMiddleware, HandlerDebugMiddleware

        dispatcher.update.middleware(DataDebugMiddleware())
        dispatcher.update.middleware(HandlerDebugMiddleware())

    if CONFIG.memory_debug:
        dispatcher.update.middleware(TracemallocMiddleware())
