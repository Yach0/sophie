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
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="greetings")


async def pre_setup() -> None:
    router.message.outer_middleware(LeaveUserMiddleware())
    router.message.outer_middleware(NewUserMiddleware())


module_manifest = ModuleManifest(
    name="greetings",
    bot_router=router,
    handlers=(
        EnableWelcomeHandlerABC,
        SetWelcomeMessageHandler,
        SetJoinRequestMessageHandler,
        DelJoinRequestMessageHandler,
        WelcomeSettingsShowHandler,
        CleanServiceHandlerABC,
        CleanWelcomeHandlerABC,
    ),
    pre_setup=pre_setup,
    title=l_("Greetings"),
    emoji="🙋‍♂️",
    description=l_("Welcome new users to your chat"),
    info=l_(
        "This module helps you to welcome new users automatically, while keeping the chat clean."
        "\nIf you want to enforce captcha / rules verification, please see 'Welcome Security' module instead."
    ),
)
