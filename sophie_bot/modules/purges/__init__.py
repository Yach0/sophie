from __future__ import annotations

from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.purges.handlers.button import LegacyDelMsgButton
from sophie_bot.modules.purges.handlers.delete import DelMsgCmdHandler
from sophie_bot.modules.purges.handlers.purge import PurgeMessagesHandler
from sophie_bot.modules.purges.magic_handlers.modern_filter import DelMsgModern
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_DELETE_MESSAGE_BUTTON_PREFIX,
    LegacyButtonAction,
)
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

router = Router(name="purges")


module_manifest = ModuleManifest(
    name="purges",
    bot_router_factory=lambda: Router(name=router.name),
    handlers=(
        DelMsgCmdHandler,
        PurgeMessagesHandler,
        LegacyDelMsgButton,
    ),
    legacy_buttons=(LegacyButtonAction("delmsg", LEGACY_DELETE_MESSAGE_BUTTON_PREFIX),),
    title=l_("Purges"),
    emoji="🗑",
    description=l_("Delete messages in bulk"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows administrators to delete multiple messages at once."),
            l_("Useful for cleaning up chat history and removing unwanted content."),
        )
    ),
    modern_actions=(DelMsgModern,),
)
