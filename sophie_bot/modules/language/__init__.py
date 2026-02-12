from aiogram import Router
from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.language import LanguageCallbackHandler, LanguageHandler

router = Router(name="language")

__all__ = ["router"]

__module_name__ = l_("Language")
__module_emoji__ = "🌐"
__module_description__ = l_("Language and localization settings")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows users to set their preferred language for bot interactions."),
    )
)

__handlers__ = (LanguageHandler, LanguageCallbackHandler)
