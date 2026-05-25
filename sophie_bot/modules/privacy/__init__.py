from types import ModuleType

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest, get_module_manifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .callbacks import PrivacyMenuCallback
from .handlers.export import EXPORTABLE_MODULES, TriggerExport
from .handlers.privacy import PrivacyMenu

__all__ = ["PrivacyMenuCallback", "module_manifest"]

router = Router(name="privacy")


async def post_setup(modules: dict[str, ModuleType]) -> None:
    EXPORTABLE_MODULES.clear()
    for module in modules.values():
        if export_data := get_module_manifest(module).export:
            EXPORTABLE_MODULES.append(export_data)


module_manifest = ModuleManifest(
    name="privacy",
    bot_router=router,
    handlers=(PrivacyMenu, TriggerExport),
    post_setup=post_setup,
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
