from __future__ import annotations

from aiogram import Router

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.greetings.handlers.enablewelcome import EnableWelcomeHandlerABC
from sophie_bot.modules.greetings.handlers.set_join_request import (
    DelJoinRequestMessageHandler,
    SetJoinRequestMessageHandler,
)
from sophie_bot.modules.greetings.handlers.status_cleanservice import (
    CleanServiceHandlerABC,
)
from sophie_bot.modules.greetings.handlers.status_cleanwelcome import (
    CleanWelcomeHandlerABC,
)
from sophie_bot.modules.greetings.handlers.status_greetings import (
    SetWelcomeMessageHandler,
)
from sophie_bot.modules.greetings.handlers.status_overall import (
    WelcomeSettingsShowHandler,
)
from sophie_bot.modules.greetings.middlewares.leave_user import LeaveUserMiddleware
from sophie_bot.modules.greetings.middlewares.new_user import NewUserMiddleware
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="greetings")


async def setup_bot(bot_router: Router, _services: ApplicationServices) -> None:
    bot_router.message.outer_middleware(LeaveUserMiddleware())
    bot_router.message.outer_middleware(NewUserMiddleware())


module_manifest = ModuleManifest(
    name="greetings",
    bot_router_factory=lambda: Router(name=router.name),
    handlers=(
        EnableWelcomeHandlerABC,
        SetWelcomeMessageHandler,
        SetJoinRequestMessageHandler,
        DelJoinRequestMessageHandler,
        WelcomeSettingsShowHandler,
        CleanServiceHandlerABC,
        CleanWelcomeHandlerABC,
    ),
    setup_bot=setup_bot,
    title=l_("Greetings"),
    emoji="🙋‍♂️",
    description=l_("Welcome new users to your chat"),
    info=l_(
        "This module helps you to welcome new users automatically, while keeping the chat clean."
        "\nIf you want to enforce captcha / rules verification, please see 'Welcome Security' module instead."
    ),
)
