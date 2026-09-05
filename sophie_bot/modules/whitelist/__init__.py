from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.whitelist.handlers import UnwhitelistUserHandler, WhitelistUserHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="whitelist")

module_manifest = ModuleManifest(
    name="whitelist",
    bot_router=router,
    handlers=(WhitelistUserHandler, UnwhitelistUserHandler),
    title=l_("Global User Whitelist"),
    emoji="🕊️",
    description=l_("Manage global exemptions from automated moderation"),
    info=LazyProxy(
        lambda: Doc(
            l_("The global user whitelist exempts a Telegram user from Sophie's automated moderation in every chat."),
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
)
