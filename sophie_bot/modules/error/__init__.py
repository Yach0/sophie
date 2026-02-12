from aiogram import Router
from stfu_tg import Doc

from sophie_bot.filters.user_status import IsOP
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_
from .handlers.crash_handler import crash_handler
from .handlers.error import SophieErrorHandler
from ...filters.cmd import CMDFilter
from ...middlewares import try_localization_middleware

router = Router(name="error")

__module_name__ = l_("Error")
__module_emoji__ = "🚫"
__module_description__ = l_("Error handling and reporting")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Handles errors and exceptions that occur during bot operation."),
        l_("Provides error reporting and recovery mechanisms."),
    )
)
__exclude_public__ = True


async def __pre_setup__():
    router.message.register(crash_handler, CMDFilter("op_crash"), IsOP(True))

    router.error.middleware(try_localization_middleware)
    router.error.register(SophieErrorHandler)
