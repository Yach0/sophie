from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.notes.utils.buttons_processor.legacy import BUTTONS
from sophie_bot.modules.rules.handlers.get import GetRulesHandler
from sophie_bot.modules.rules.handlers.legacy_button import LegacyRulesButton
from sophie_bot.modules.rules.handlers.reset import ResetRulesHandler
from sophie_bot.modules.rules.handlers.set import SetRulesHandler
from sophie_bot.modules.rules.magic_handlers.filter import get_filter
from sophie_bot.modules.rules.magic_handlers.modern_filter import SendRulesAction
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router

__all__ = ("api_router",)

__module_name__ = l_("Rules")
__module_emoji__ = "🪧"
__module_description__ = l_("Set and display chat rules")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows administrators to set rules for their chats."),
        l_("Users can view the rules at any time using the rules command."),
    )
)

__filters__ = get_filter()
__modern_actions__ = (SendRulesAction,)

router = Router(name="rules")

BUTTONS.update({"rules": "btn_rules"})


__handlers__ = (
    SetRulesHandler,
    GetRulesHandler,
    ResetRulesHandler,
    LegacyRulesButton,
)

module_manifest = ModuleManifest(
    name="rules",
    bot_router=router,
    api_router=api_router,
    handlers=__handlers__,
    metadata={
        "name": __module_name__,
        "emoji": __module_emoji__,
        "description": __module_description__,
        "info": __module_info__,
        "filter_actions": __filters__,
        "modern_actions": __modern_actions__,
    },
)
