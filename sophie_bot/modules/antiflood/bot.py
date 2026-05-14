from __future__ import annotations

from aiogram import Router

from sophie_bot.modules.antiflood.action_config import (
    AntifloodActionCallback,
    AntifloodActionCancel,
    AntifloodActionDone,
    AntifloodActionSettings,
    AntifloodActionSetup,
    AntifloodActionWizard,
)
from sophie_bot.modules.antiflood.handlers import (
    AntifloodInfoHandler,
    AntifloodSetCountHandler,
    EnableAntifloodHandler,
)
from sophie_bot.modules.antiflood.middlewares.enforcer import AntifloodEnforcerMiddleware

router = Router(name="antiflood")

handlers = (
    AntifloodInfoHandler,
    EnableAntifloodHandler,
    AntifloodSetCountHandler,
    AntifloodActionWizard,
    AntifloodActionCallback,
    AntifloodActionSetup,
    AntifloodActionDone,
    AntifloodActionCancel,
    AntifloodActionSettings,
)


async def setup_bot_transport() -> None:
    router.message.outer_middleware(AntifloodEnforcerMiddleware())
