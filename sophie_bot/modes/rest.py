from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from sophie_bot.config import CONFIG, Config
from sophie_bot.runtime import build_rest_runtime
from sophie_bot.services.db import DatabaseResources
from sophie_bot.services.rest import create_app
from sophie_bot.startup import initialize_rest_mode
from sophie_bot.utils.logger import log


def create_rest_app(
    *,
    config: Config = CONFIG,
    database: DatabaseResources | None = None,
) -> FastAPI:
    app = create_app(config)

    @asynccontextmanager
    async def lifespan(active_app: FastAPI) -> AsyncIterator[None]:
        log.info("Starting up Sophie API...")
        async with build_rest_runtime(active_app, config=config, database=database) as runtime:
            await initialize_rest_mode(runtime)
            yield
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
