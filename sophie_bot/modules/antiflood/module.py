from __future__ import annotations

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.antiflood.api import api_router
from sophie_bot.modules.antiflood.bot import handlers, router, setup_bot_transport
from sophie_bot.utils.i18n import lazy_gettext as l_


module_manifest = ModuleManifest(
    name="antiflood",
    bot_router=router,
    api_router=api_router,
    handlers=handlers,
    pre_setup=setup_bot_transport,
    title=l_("Antiflood"),
    emoji="📈",
    description=l_("Protect your chat from message flooding"),
)
