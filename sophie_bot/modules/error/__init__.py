from __future__ import annotations

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules import ModuleManifest
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from ...filters.cmd import CMDFilter
from ...middlewares import create_try_localization_middleware
from .handlers.crash_handler import crash_handler
from .handlers.error import SophieErrorHandler

router = Router(name="error")


async def setup_bot(router: Router, services: ApplicationServices) -> None:
    router.message.register(crash_handler, CMDFilter("op_crash"), IsOP(True))
    router.error.middleware(create_try_localization_middleware(services))
    router.error.register(SophieErrorHandler)


module_manifest = ModuleManifest(
    name="error",
    bot_router_factory=lambda: Router(name=router.name),
    setup_bot=setup_bot,
    title=l_("Error"),
    emoji="🚫",
    description=l_("Error handling and reporting"),
    info=LazyProxy(
        lambda: Doc(
            l_("Handles errors and exceptions that occur during bot operation."),
            l_("Provides error reporting and recovery mechanisms."),
        )
    ),
    exclude_public=True,
)
