from __future__ import annotations

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.antiflood.api import api_router
from sophie_bot.modules.antiflood.bot import handlers, router, setup_bot_transport
from sophie_bot.utils.i18n import lazy_gettext as l_

__module_name__ = l_("Antiflood")
__module_emoji__ = "📈"
__module_description__ = l_("Protect your chat from message flooding")

module_manifest = ModuleManifest(
    name="antiflood",
    bot_router=router,
    api_router=api_router,
    handlers=handlers,
    pre_setup=setup_bot_transport,
    metadata={
        "name": __module_name__,
        "emoji": __module_emoji__,
        "description": __module_description__,
    },
)
