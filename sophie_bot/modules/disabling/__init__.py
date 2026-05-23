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


module_manifest = ModuleManifest(
    name="disabling",
    bot_router=router,
    api_router=api_router,
    handlers=(
        ListDisableable,
        ListDisabled,
        DisableHandler,
        EnableHandler,
        EnableAllHandler,
        DisableAllCbHandler,
    ),
    title=l_("Disabling"),
    emoji="🚫",
    description=l_("Disable commands in chats"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows administrators to disable specific commands in their chats."),
            l_("Useful for restricting bot functionality to only necessary commands."),
        )
    ),
    export=export_disabled,
)
