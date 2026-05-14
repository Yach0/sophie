from types import ModuleType

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from ...filters.admin_rights import UserRestricting
from ...filters.chat_status import ChatTypeFilter
from ...filters.cmd import CMDFilter
from .callbacks import PrivacyMenuCallback
from .handlers.export import EXPORTABLE_MODULES, TriggerExport
from .handlers.privacy import PrivacyMenu

router = Router(name="info")


__module_name__ = l_("Privacy")
__module_emoji__ = "🕵️‍♂️️"
__module_description__ = l_("Data protection")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Manages user privacy and data protection settings."),
        l_("Allows users to export their data and control privacy preferences."),
    )
)


async def __pre_setup__():
    router.message.register(PrivacyMenu, CMDFilter("privacy"), ChatTypeFilter("private"))
    router.callback_query.register(PrivacyMenu, PrivacyMenuCallback.filter())

    router.message.register(TriggerExport, CMDFilter("export"), ChatTypeFilter("private"), UserRestricting(admin=True))


async def __post_setup__(modules: dict[str, ModuleType]):
    EXPORTABLE_MODULES.clear()
    for module in modules.values():
        if hasattr(module, "__export__"):
            EXPORTABLE_MODULES.append(module)
