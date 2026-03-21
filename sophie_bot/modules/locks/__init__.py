from __future__ import annotations

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules.locks.handlers.lock import LockHandler
from sophie_bot.modules.locks.handlers.lockable import ListLockableHandler
from sophie_bot.modules.locks.handlers.locklanguages import ListLockLanguagesHandler
from sophie_bot.modules.locks.handlers.locks_list import LocksListHandler
from sophie_bot.modules.locks.handlers.locksticker import LockStickerHandler
from sophie_bot.modules.locks.handlers.unlock import UnlockHandler
from sophie_bot.modules.locks.middlewares.enforcer import LocksEnforcerMiddleware
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router

__module_name__ = l_("Locks")
__module_emoji__ = "🔓"
__module_description__ = l_("Lock specific message types in chats")
__module_info__ = l_(
    lambda: Doc(
        l_("Allows administrators to lock specific types of messages in chats."),
        l_(
            "Prevents users from sending certain content types like stickers, GIFs, URLs, sticker packs, and languages."
        ),
    )
)

router = Router(name="locks")

__handlers__ = (
    ListLockableHandler,
    LockHandler,
    UnlockHandler,
    LockStickerHandler,
    LocksListHandler,
    ListLockLanguagesHandler,
)

__all__ = (
    api_router,
    router,
    __handlers__,
)


async def __pre_setup__() -> None:
    router.message.outer_middleware(LocksEnforcerMiddleware())
