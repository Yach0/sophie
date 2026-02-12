from aiogram import Router
from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.pin import PinHandler
from .handlers.unpin import UnpinHandler

router = Router(name="pins")

__module_name__ = l_("Pins")
__module_emoji__ = "📌"
__module_description__ = l_("Pin and unpin messages in chats")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows administrators to pin important messages in the chat."),
        l_("Also provides the ability to unpin messages when needed."),
    )
)

__handlers__ = [PinHandler, UnpinHandler]
