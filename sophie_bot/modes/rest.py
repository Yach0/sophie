from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from sophie_bot.config import CONFIG
from sophie_bot.runtime import build_rest_runtime
from sophie_bot.services.health import heartbeat_loop
from sophie_bot.services.rest import init_api_routers
from sophie_bot.startup import initialize_rest_mode
from sophie_bot.utils.logger import log


def create_rest_app() -> FastAPI:
    runtime = build_rest_runtime()
    app = runtime.app

    @asynccontextmanager
    async def lifespan(active_app: FastAPI):
        log.info("Starting up Sophie API...")

        await initialize_rest_mode(runtime)
        init_api_routers(active_app, runtime.loaded_modules.api_routers)

        heartbeat_task = asyncio.create_task(heartbeat_loop(CONFIG.mode))

        yield

        heartbeat_task.cancel()
        log.info("Shutting down Sophie API...")

    app.router.lifespan_context = lifespan
    return app


def start_rest_mode() -> None:
    if CONFIG.dev_reload:
        log.info("Starting REST API with hot-reload enabled...")
        uvicorn.run(
            "sophie_bot.modes.rest:create_rest_app",
            host=CONFIG.api_listen,
            port=CONFIG.api_port,
            factory=True,
            reload=True,
            reload_dirs=["sophie_bot"],
        )
    else:
        uvicorn.run(create_rest_app(), host=CONFIG.api_listen, port=CONFIG.api_port)
