from aiogram import Router

from sophie_bot.modules.antiflood.action_config import (
    AntifloodActionCallback,
    AntifloodActionCancel,
    AntifloodActionDone,
    AntifloodActionSettings,
    AntifloodActionSetup,
    AntifloodActionWizard,
)
from sophie_bot.modules.antiflood.api import api_router
from sophie_bot.modules.antiflood.handlers import (
    AntifloodInfoHandler,
    AntifloodSetCountHandler,
    EnableAntifloodHandler,
)
from sophie_bot.modules.antiflood.middlewares.enforcer import AntifloodEnforcerMiddleware
from sophie_bot.utils.i18n import lazy_gettext as l_

__all__ = [
    "router",
    "api_router",
    "__module_name__",
    "__module_emoji__",
    "__module_description__",
    "__module_info__",
    "__handlers__",
    "__pre_setup__",
]

router = Router(name="antiflood")

__module_name__ = l_("Antiflood")
__module_emoji__ = "📈"
__module_description__ = l_("Protect your chat from message flooding")

__handlers__ = (
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


async def __pre_setup__() -> None:
    """Register middleware and any manual handlers."""
    router.message.outer_middleware(AntifloodEnforcerMiddleware())
