from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.pin import PinHandler
from .handlers.unpin import UnpinHandler

router = Router(name="pins")


module_manifest = ModuleManifest(
    name="pins",
    bot_router=router,
    handlers=[PinHandler, UnpinHandler],
    title=l_("Pins"),
    emoji="📌",
    description=l_("Pin and unpin messages in chats"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows administrators to pin important messages in the chat."),
            l_("Also provides the ability to unpin messages when needed."),
        )
    ),
)
