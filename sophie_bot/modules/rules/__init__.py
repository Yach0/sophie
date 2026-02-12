from aiogram import Router
from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router as api_router
from sophie_bot.modules.notes.utils.buttons_processor.legacy import BUTTONS
from sophie_bot.modules.rules.handlers.get import GetRulesHandler
from sophie_bot.modules.rules.handlers.legacy_button import LegacyRulesButton
from sophie_bot.modules.rules.handlers.reset import ResetRulesHandler
from sophie_bot.modules.rules.handlers.set import SetRulesHandler
from sophie_bot.modules.rules.magic_handlers.filter import get_filter
from sophie_bot.modules.rules.magic_handlers.modern_filter import SendRulesAction

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
