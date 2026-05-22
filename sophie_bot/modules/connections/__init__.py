from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.notes.utils.buttons_processor.legacy import BUTTONS
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers import (
    AllowUsersConnectCmd,
    ConnectCallback,
    ConnectDMCmd,
    ConnectGroupCmd,
    DisconnectCmd,
    StartConnectHandler,
)

BUTTONS.update({"connect": "btn_connect_start"})

router = Router(name="connections")


module_manifest = ModuleManifest(
    name="connections",
    bot_router=router,
    handlers=(
        ConnectDMCmd,
        ConnectGroupCmd,
        ConnectCallback,
        StartConnectHandler,
        DisconnectCmd,
        AllowUsersConnectCmd,
    ),
    title=l_("Connections"),
    emoji="🔗",
    description=l_("Connect to chats from private messages"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows users to connect to chats from private messages."),
            l_("Enables managing chat settings and using commands without being in the chat."),
        )
    ),
)
