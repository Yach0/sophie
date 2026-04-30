from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from aiogram import Dispatcher
from fastapi import FastAPI

from sophie_bot.config import CONFIG
from sophie_bot.services.rest import app, init_api_routers
from sophie_bot.startup import init_database, init_modules_rest
from sophie_bot.utils.logger import log


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up Sophie API...")

    dp = Dispatcher()
    await init_database()
    await init_modules_rest(dp)

    init_api_routers(app)

    yield
    log.info("Shutting down Sophie API...")


app.router.lifespan_context = lifespan


def start_rest_mode() -> None:
    if CONFIG.dev_reload:
        log.info("Starting REST API with hot-reload enabled...")
        # For reload to work, we must pass app as import string
        uvicorn.run(
            "sophie_bot.modes.rest:app",
            host=CONFIG.api_listen,
            port=CONFIG.api_port,
            reload=True,
            reload_dirs=["sophie_bot"],
        )
    else:
        uvicorn.run(app, host=CONFIG.api_listen, port=CONFIG.api_port)
