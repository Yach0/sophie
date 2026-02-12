from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router

__all__ = ["api_router"]

__module_name__ = l_("Logging")
__module_emoji__ = "📋"
__module_description__ = l_("Log chat events and actions")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Logs various chat events and actions for moderation purposes."),
        l_("Helps administrators keep track of what happens in their chats."),
    )
)

__exclude_public__ = True
