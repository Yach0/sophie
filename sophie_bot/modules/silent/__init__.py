from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.silent import SilentHandler

router = Router(name="silent")

__all__ = ["router"]

__module_name__ = l_("Silent Mode")
__module_emoji__ = "🤫"
__module_description__ = l_("Auto-delete command messages and replies")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Automatically deletes your previous command and the bot's reply when you send a new command."),
    )
)

__handlers__ = (SilentHandler,)

module_manifest = ModuleManifest(
    name="silent",
    bot_router=router,
    handlers=__handlers__,
    metadata={
        "name": __module_name__,
        "emoji": __module_emoji__,
        "description": __module_description__,
        "info": __module_info__,
    },
)
