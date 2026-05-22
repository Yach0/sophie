from __future__ import annotations

import ssl
from typing import Optional

from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    ip_filter_middleware,
    setup_application,
)
from aiogram.webhook.security import IPFilter
from aiohttp.web import run_app
from aiohttp.web_app import Application

from sophie_bot.config import CONFIG
from sophie_bot.middlewares import enable_middlewares, set_metrics_middleware
from sophie_bot.runtime import BotModeRuntime, build_bot_runtime
from sophie_bot.startup import initialize_bot_mode
from sophie_bot.utils.logger import log


ALLOWED_UPDATES = [
    "message",
    "edited_message",
    # 'channel_post',
    # 'edited_channel_post',
    "inline_query",
    # 'chosen_inline_result',
    "callback_query",
    # 'shipping_query',
    # 'pre_checkout_query',
    # 'poll',
    # 'poll_answer',
    "my_chat_member",
    "chat_member",
    "chat_join_request",
]


def _configure_bot_startup(runtime: BotModeRuntime) -> None:
    dispatcher = runtime.bot_runtime.dispatcher

    @dispatcher.startup()
    async def bot_start() -> None:
        await initialize_bot_mode(runtime)

        # Initialize metrics system if enabled
        if CONFIG.metrics_enable:
            await _init_metrics()

        enable_middlewares(dispatcher)


async def _init_metrics() -> None:
    """Initialize the metrics system"""
    try:
        from sophie_bot.metrics import MetricsMiddleware, start_background_tasks

        await start_background_tasks()

        # Create and set middleware
        metrics_middleware = MetricsMiddleware(CONFIG)
        set_metrics_middleware(metrics_middleware)

        log.info("Metrics system initialized successfully")

    except Exception as e:
        log.error("Failed to initialize metrics system", error=str(e))
        if CONFIG.debug_mode != "off":
            raise


def start_bot_mode() -> None:
    if CONFIG.dev_reload:
        from sophie_bot.utils.dev_runner import run_with_reload

        run_with_reload("bot")
        return

    runtime = build_bot_runtime()
    _configure_bot_startup(runtime)
    bot = runtime.bot_runtime.bot
    dispatcher = runtime.bot_runtime.dispatcher

    if not CONFIG.webhooks_enable:
        dispatcher.run_polling(
            bot,
            allowed_updates=ALLOWED_UPDATES,
        )
    else:
        app = Application()
        SimpleRequestHandler(
            dispatcher=dispatcher,
            bot=bot,
            handle_in_background=CONFIG.webhooks_handle_in_background,
            secret_token=CONFIG.webhooks_secret_token,
        ).register(app, path=CONFIG.webhooks_path)

        if CONFIG.webhooks_filter_ips:
            log.info("Filtering IP addresses", ips=CONFIG.webhooks_allowed_networks)
            app.middlewares.append(ip_filter_middleware(IPFilter(CONFIG.webhooks_allowed_networks)))  # type: ignore

        setup_application(app, dispatcher, bot=bot)

        ssl_context: Optional[ssl.SSLContext]
        if CONFIG.webhooks_https_certificate:
            log.info("Using HTTPs!")

            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(CONFIG.webhooks_https_certificate, CONFIG.webhooks_https_certificate_key)
        else:
            ssl_context = None
            log.warn("Using HTTP (use it only for reverse-proxy or development)!")

        run_app(app, host=CONFIG.webhooks_listen, port=CONFIG.webhooks_port, ssl_context=ssl_context)
