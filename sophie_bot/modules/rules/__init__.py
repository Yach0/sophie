from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.rules.handlers.get import GetRulesHandler
from sophie_bot.modules.rules.handlers.legacy_button import LegacyRulesButton
from sophie_bot.modules.rules.handlers.reset import ResetRulesHandler
from sophie_bot.modules.rules.handlers.set import SetRulesHandler
from sophie_bot.modules.rules.magic_handlers.filter import get_filter
from sophie_bot.modules.rules.magic_handlers.modern_filter import SendRulesAction
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_RULES_BUTTON_PREFIX,
    LegacyButtonAction,
    register_legacy_button_actions,
)
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import api_router

__all__ = ("api_router",)


router = Router(name="rules")

register_legacy_button_actions(LegacyButtonAction("rules", LEGACY_RULES_BUTTON_PREFIX))


module_manifest = ModuleManifest(
    name="rules",
    bot_router=router,
    api_router=api_router,
    handlers=(
        SetRulesHandler,
        GetRulesHandler,
        ResetRulesHandler,
        LegacyRulesButton,
    ),
    title=l_("Rules"),
    emoji="🪧",
    description=l_("Set and display chat rules"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows administrators to set rules for their chats."),
            l_("Users can view the rules at any time using the rules command."),
        )
    ),
    filter_actions=get_filter(),
    modern_actions=(SendRulesAction,),
)
