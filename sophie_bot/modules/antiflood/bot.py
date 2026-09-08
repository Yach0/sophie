from __future__ import annotations

from typing import Any

from aiogram import Router

from sophie_bot.modules.antiflood.action_config import (
    AntifloodActionCallback,
    AntifloodActionInput,
    AntifloodActionInputCleanup,
    AntifloodActionWizard,
)
from sophie_bot.modules.antiflood.handlers import (
    AntifloodInfoHandler,
    AntifloodSetCountHandler,
    EnableAntifloodHandler,
)
from sophie_bot.modules.antiflood.middlewares.enforcer import AntifloodEnforcerMiddleware
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.handlers import SophieBaseHandler

router = Router(name="antiflood")

handlers: tuple[type[SophieBaseHandler[Any]], ...] = (
    AntifloodInfoHandler,
    EnableAntifloodHandler,
    AntifloodSetCountHandler,
    AntifloodActionWizard,
    AntifloodActionCallback,
    AntifloodActionInput,
    AntifloodActionInputCleanup,
)


async def setup_bot_transport(router: Router, services: ApplicationServices) -> None:
    router.message.outer_middleware(AntifloodEnforcerMiddleware(services))
