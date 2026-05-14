from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.disabling.export import export_disabled
from sophie_bot.modules.disabling.handlers.disable import DisableHandler
from sophie_bot.modules.disabling.handlers.disable_able import ListDisableable
from sophie_bot.modules.disabling.handlers.disabled import ListDisabled
from sophie_bot.modules.disabling.handlers.enable import EnableHandler
from sophie_bot.modules.disabling.handlers.enable_all import (
    DisableAllCbHandler,
    EnableAllHandler,
)
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_
from .api import api_router

__all__ = ("api_router",)

router = Router(name="Disable")


__module_name__ = l_("Disabling")
__module_emoji__ = "🚫"
__module_description__ = l_("Disable commands in chats")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows administrators to disable specific commands in their chats."),
        l_("Useful for restricting bot functionality to only necessary commands."),
    )
)

__export__ = export_disabled


async def __pre_setup__():
    router.message.register(ListDisableable, *ListDisableable.filters())
    router.message.register(ListDisabled, *ListDisabled.filters())
    router.message.register(DisableHandler, *DisableHandler.filters())
    router.message.register(EnableHandler, *EnableHandler.filters())
    router.message.register(EnableAllHandler, *EnableAllHandler.filters())

    router.callback_query.register(DisableAllCbHandler, *DisableAllCbHandler.filters())


module_manifest = ModuleManifest(
    name="disabling",
    bot_router=router,
    api_router=api_router,
    pre_setup=__pre_setup__,
    metadata={
        "name": __module_name__,
        "emoji": __module_emoji__,
        "description": __module_description__,
        "info": __module_info__,
        "export": __export__,
    },
)
