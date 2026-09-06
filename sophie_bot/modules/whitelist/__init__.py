from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.whitelist.handlers import (
    UnwhitelistUserHandler,
    WhitelistedUsersHandler,
    WhitelistPageHandler,
    WhitelistRemoveHandler,
    WhitelistUserHandler,
)
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="whitelist")

module_manifest = ModuleManifest(
    name="whitelist",
    bot_router=router,
    handlers=(
        WhitelistUserHandler,
        UnwhitelistUserHandler,
        WhitelistedUsersHandler,
        WhitelistPageHandler,
        WhitelistRemoveHandler,
    ),
    title=l_("Group whitelist"),
    emoji="🕊️",
    description=l_("Manage the group whitelist for automated moderation"),
    info=LazyProxy(
        lambda: Doc(
            l_(
                "The group whitelist exempts a Telegram user from Sophie's automated moderation "
                "throughout the current group. Entries do not apply in other groups."
            ),
            l_(
                "It covers Welcome Security, locks, automated filters and actions, antiflood, spam detection, "
                "AI moderation, and automatic federation or community ban checks."
            ),
            l_(
                "It does not grant administrator rights, authorize admin commands, or prevent an administrator "
                "from applying a direct moderation command."
            ),
        )
    ),
    advertise_wiki_page=True,
)
