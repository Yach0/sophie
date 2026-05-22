from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.promotes.handlers.demote import DemoteUserHandler
from sophie_bot.modules.promotes.handlers.promote import PromoteUserHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


router = Router(name="promotes")


module_manifest = ModuleManifest(
    name="promotes",
    bot_router=router,
    handlers=(
        PromoteUserHandler,
        DemoteUserHandler,
    ),
    title=l_("Promotes"),
    emoji="⭐️",
    description=l_("Manage user promotions in chats"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows administrators to promote and demote users in chats."),
            l_("Manage user permissions and administrative privileges."),
        )
    ),
)
