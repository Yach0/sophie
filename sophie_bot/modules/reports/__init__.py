from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.report import ReportHandler

router = Router(name="reports")


module_manifest = ModuleManifest(
    name="reports",
    bot_router=router,
    handlers=(ReportHandler,),
    title=l_("Reports"),
    emoji="📢",
    description=l_("Report messages to chat admins"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows users to report messages to chat administrators."),
            l_("Admins will be notified about reported messages and can take appropriate action."),
        )
    ),
)
