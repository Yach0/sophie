from __future__ import annotations

import asyncio
import ssl
from typing import Any, cast

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, ip_filter_middleware, setup_application
from aiogram.webhook.security import IPFilter
from aiohttp.typedefs import Middleware
from aiohttp.web_app import Application
from aiohttp.web_runner import AppRunner, TCPSite

from sophie_bot.config import CONFIG
from sophie_bot.middlewares import enable_middlewares
from sophie_bot.runtime import BotModeRuntime, build_bot_runtime
from sophie_bot.services.health import heartbeat_loop
from sophie_bot.startup import initialize_bot_mode
from sophie_bot.utils.logger import log

ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "inline_query",
    "callback_query",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
]


def _init_metrics(runtime: BotModeRuntime) -> Any | None:
    if not CONFIG.metrics_enable:
        return None
    try:
        from sophie_bot.metrics import MetricsMiddleware, start_background_tasks

        runtime.services.background_tasks.update(start_background_tasks())
        log.info("Metrics system initialized successfully")
        return MetricsMiddleware(CONFIG)
    except Exception as error:
        log.error("Failed to initialize metrics system", error=str(error))
        if CONFIG.debug_mode != "off":
            raise
        return None


async def _prepare_runtime(runtime: BotModeRuntime) -> None:
    await initialize_bot_mode(runtime)
    enable_middlewares(runtime.dispatcher, runtime.services, _init_metrics(runtime))
    heartbeat_task = asyncio.create_task(heartbeat_loop(CONFIG.mode, redis=runtime.services.redis))
    runtime.services.background_tasks.add(heartbeat_task)
    heartbeat_task.add_done_callback(runtime.services.background_tasks.discard)


async def _polling_main() -> None:
    async with build_bot_runtime() as runtime:
        await _prepare_runtime(runtime)
        await runtime.dispatcher.start_polling(
            runtime.services.bot,
            allowed_updates=ALLOWED_UPDATES,
            close_bot_session=False,
        )


def _ssl_context() -> ssl.SSLContext | None:
    if not CONFIG.webhooks_https_certificate:
        log.warning("Using HTTP (use it only for reverse-proxy or development)!")
        return None
    log.info("Using HTTPS!")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CONFIG.webhooks_https_certificate, CONFIG.webhooks_https_certificate_key)
    return context


async def _webhook_main() -> None:
    async with build_bot_runtime() as runtime:
        await _prepare_runtime(runtime)
        app = Application()
        SimpleRequestHandler(
            dispatcher=runtime.dispatcher,
            bot=runtime.services.bot,
            handle_in_background=CONFIG.webhooks_handle_in_background,
            secret_token=CONFIG.webhooks_secret_token,
        ).register(app, path=CONFIG.webhooks_path)
        if CONFIG.webhooks_filter_ips:
            log.info("Filtering IP addresses", ips=CONFIG.webhooks_allowed_networks)
            app.middlewares.append(
                cast(
                    Middleware,
                    ip_filter_middleware(IPFilter(CONFIG.webhooks_allowed_networks)),
                )
            )
        setup_application(app, runtime.dispatcher, bot=runtime.services.bot)
        runner = AppRunner(app)
        await runner.setup()
        site = TCPSite(
            runner,
            host=CONFIG.webhooks_listen,
            port=CONFIG.webhooks_port,
            ssl_context=_ssl_context(),
        )
        await site.start()
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


def start_bot_mode() -> None:
    if CONFIG.dev_reload:
        from sophie_bot.utils.dev_runner import run_with_reload

        run_with_reload("bot")
        return
    try:
        asyncio.run(_webhook_main() if CONFIG.webhooks_enable else _polling_main())
    except (KeyboardInterrupt, SystemExit):
        pass
