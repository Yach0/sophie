from aiogram import Router

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.greetings.handlers.enablewelcome import EnableWelcomeHandlerABC
from sophie_bot.modules.greetings.handlers.status_cleanservice import (
    CleanServiceHandlerABC,
)
from sophie_bot.modules.greetings.handlers.status_cleanwelcome import (
    CleanWelcomeHandlerABC,
)
from sophie_bot.modules.greetings.handlers.set_join_request import (
    DelJoinRequestMessageHandler,
    SetJoinRequestMessageHandler,
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

__module_name__ = l_("Greetings")
__module_emoji__ = "🙋‍♂️"
__module_description__ = l_("Welcome new users to your chat")
__module_info__ = l_(
    "This module helps you to welcome new users automatically, while keeping the chat clean."
    "\nIf you want to enforce captcha / rules verification, please see 'Welcome Security' module instead."
)

router = Router(name="greetings")


__handlers__ = (
    EnableWelcomeHandlerABC,
    SetWelcomeMessageHandler,
    SetJoinRequestMessageHandler,
    DelJoinRequestMessageHandler,
    WelcomeSettingsShowHandler,
    CleanServiceHandlerABC,
    CleanWelcomeHandlerABC,
)


async def __pre_setup__():
    router.message.outer_middleware(LeaveUserMiddleware())
    router.message.outer_middleware(NewUserMiddleware())


module_manifest = ModuleManifest(
    name="greetings",
    bot_router=router,
    handlers=__handlers__,
    pre_setup=__pre_setup__,
    metadata={
        "name": __module_name__,
        "emoji": __module_emoji__,
        "description": __module_description__,
        "info": __module_info__,
    },
)
