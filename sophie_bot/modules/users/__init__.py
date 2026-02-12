from aiogram import Router
from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.adminlist import AdminListHandler
from .handlers.id import ShowIDHandler
from .handlers.info import UserInfoHandler
from .stats import users_stats

router = Router(name="users")
__module_name__ = l_("Users")
__module_emoji__ = "🫂"
__module_description__ = l_("User information and management")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Provides commands to get information about users and chat members."),
        l_("Includes admin list, user IDs, and detailed user information."),
    )
)
__stats__ = users_stats

__handlers__ = (
    ShowIDHandler,
    AdminListHandler,
    UserInfoHandler,
)

__all__ = (
    "router",
    "__module_name__",
    "__module_emoji__",
    "__module_description__",
    "__module_info__",
    "__handlers__",
    "__stats__",
)
