from aiogram import Router
from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.report import ReportHandler

router = Router(name="reports")

__module_name__ = l_("Reports")
__module_emoji__ = "📢"
__module_description__ = l_("Report messages to chat admins")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows users to report messages to chat administrators."),
        l_("Admins will be notified about reported messages and can take appropriate action."),
    )
)

__handlers__ = (ReportHandler,)
