from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router

__all__ = ["api_router"]


module_manifest = ModuleManifest(
    name="logging",
    api_router=api_router,
    title=l_("Logging"),
    emoji="📋",
    description=l_("Log chat events and actions"),
    info=LazyProxy(
        lambda: Doc(
            l_("Logs various chat events and actions for moderation purposes."),
            l_("Helps administrators keep track of what happens in their chats."),
        )
    ),
    exclude_public=True,
)
