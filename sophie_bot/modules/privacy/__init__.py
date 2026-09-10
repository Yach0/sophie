from __future__ import annotations

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .callbacks import PrivacyMenuCallback
from .handlers.export import TriggerExport
from .handlers.privacy import PrivacyMenu

__all__ = ["PrivacyMenuCallback", "module_manifest"]

router = Router(name="privacy")


module_manifest = ModuleManifest(
    name="privacy",
    bot_router_factory=lambda: Router(name=router.name),
    handlers=(PrivacyMenu, TriggerExport),
    title=l_("Privacy"),
    emoji="🕵️‍♂️️",
    description=l_("Data protection"),
    info=LazyProxy(
        lambda: Doc(
            l_("Manages user privacy and data protection settings."),
            l_("Allows users to export their data and control privacy preferences."),
        )
    ),
)
