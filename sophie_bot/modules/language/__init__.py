from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.language import LanguageCallbackHandler, LanguageHandler

router = Router(name="language")

__all__ = ["router"]


module_manifest = ModuleManifest(
    name="language",
    bot_router=router,
    handlers=(LanguageHandler, LanguageCallbackHandler),
    title=l_("Language"),
    emoji="🌐",
    description=l_("Language and localization settings"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows users to set their preferred language for bot interactions."),
        )
    ),
)
