from __future__ import annotations

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.locks.handlers.lock import LockHandler
from sophie_bot.modules.locks.handlers.lockable import ListLockableHandler
from sophie_bot.modules.locks.handlers.locklanguages import ListLockLanguagesHandler
from sophie_bot.modules.locks.handlers.locks_list import LocksListHandler
from sophie_bot.modules.locks.handlers.locksticker import LockStickerHandler
from sophie_bot.modules.locks.handlers.unlock import UnlockHandler
from sophie_bot.modules.locks.handlers.unlock_all import UnlockAllCallbackHandler
from sophie_bot.modules.locks.middlewares.enforcer import LocksEnforcerMiddleware
from sophie_bot.modules.locks.middlewares.reaction_enforcer import ReactionLocksEnforcerMiddleware
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router


router = Router(name="locks")


__all__ = (
    "api_router",
    "router",
)


async def pre_setup() -> None:
    router.message.outer_middleware(LocksEnforcerMiddleware())
    router.message_reaction.outer_middleware(ReactionLocksEnforcerMiddleware())


module_manifest = ModuleManifest(
    name="locks",
    bot_router=router,
    api_router=api_router,
    handlers=(
        ListLockableHandler,
        LockHandler,
        UnlockHandler,
        UnlockAllCallbackHandler,
        LockStickerHandler,
        LocksListHandler,
        ListLockLanguagesHandler,
    ),
    pre_setup=pre_setup,
    title=l_("Locks"),
    emoji="🔓",
    description=l_("Lock specific message types in chats"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows administrators to lock specific types of messages in chats."),
            l_(
                "Prevents users from sending certain content types like stickers, GIFs, URLs, sticker packs, and languages."
            ),
        )
    ),
    advertise_wiki_page=True,
)
