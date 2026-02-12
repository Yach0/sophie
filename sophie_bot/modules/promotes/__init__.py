from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules.promotes.handlers.demote import DemoteUserHandler
from sophie_bot.modules.promotes.handlers.promote import PromoteUserHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

__module_name__ = l_("Promotes")
__module_emoji__ = "⭐️"
__module_description__ = l_("Manage user promotions in chats")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows administrators to promote and demote users in chats."),
        l_("Manage user permissions and administrative privileges."),
    )
)

router = Router(name="promotes")


__handlers__ = (
    PromoteUserHandler,
    DemoteUserHandler,
)
