from types import ModuleType

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest, get_module_manifest
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from ...filters.admin_rights import UserRestricting
from ...filters.chat_status import ChatTypeFilter
from ...filters.cmd import CMDFilter
from .callbacks import PrivacyMenuCallback
from .handlers.export import EXPORTABLE_MODULES, TriggerExport
from .handlers.privacy import PrivacyMenu

router = Router(name="info")


async def pre_setup() -> None:
    router.message.register(PrivacyMenu, CMDFilter("privacy"), ChatTypeFilter("private"))
    router.callback_query.register(PrivacyMenu, PrivacyMenuCallback.filter())

    router.message.register(TriggerExport, CMDFilter("export"), ChatTypeFilter("private"), UserRestricting(admin=True))


async def post_setup(modules: dict[str, ModuleType]) -> None:
    EXPORTABLE_MODULES.clear()
    for module in modules.values():
        if export_data := get_module_manifest(module).export:
            EXPORTABLE_MODULES.append(export_data)


module_manifest = ModuleManifest(
    name="privacy",
    bot_router=router,
    pre_setup=pre_setup,
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
