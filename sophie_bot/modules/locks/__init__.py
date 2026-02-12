from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules.locks.handlers.lockable import ListLockableHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

__module_name__ = l_("Locks")
__module_emoji__ = "🔓"
__module_description__ = l_("Lock specific message types in chats")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows administrators to lock specific types of messages in chats."),
        l_("Prevents users from sending certain content types like stickers, GIFs, or URLs."),
    )
)

router = Router(name="locks")

__handlers__ = ListLockableHandler
