from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.adminlist import AdminListHandler
from .handlers.id import ShowIDHandler
from .handlers.info import UserInfoHandler
from .stats import users_stats

router = Router(name="users")


module_manifest = ModuleManifest(
    name="users",
    bot_router=router,
    handlers=(
        ShowIDHandler,
        AdminListHandler,
        UserInfoHandler,
    ),
    title=l_("Users"),
    emoji="🫂",
    description=l_("User information and management"),
    info=LazyProxy(
        lambda: Doc(
            l_("Provides commands to get information about users and chat members."),
            l_("Includes admin list, user IDs, and detailed user information."),
        )
    ),
    stats=users_stats,
)

__all__ = (
    "router",
    "module_manifest",
)
