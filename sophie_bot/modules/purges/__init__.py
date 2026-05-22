from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.notes.utils.buttons_processor.legacy import BUTTONS
from sophie_bot.modules.purges.handlers.button import LegacyDelMsgButton
from sophie_bot.modules.purges.handlers.delete import DelMsgCmdHandler
from sophie_bot.modules.purges.handlers.purge import PurgeMessagesHandler
from sophie_bot.modules.purges.magic_handlers.filter import get_filter
from sophie_bot.modules.purges.magic_handlers.modern_filter import DelMsgModernModern
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


BUTTONS.update({"delmsg": "btn_deletemsg_cb"})

router = Router(name="purges")


module_manifest = ModuleManifest(
    name="purges",
    bot_router=router,
    handlers=(
        DelMsgCmdHandler,
        PurgeMessagesHandler,
        LegacyDelMsgButton,
    ),
    title=l_("Purges"),
    emoji="🗑",
    description=l_("Delete messages in bulk"),
    info=LazyProxy(
        lambda: Doc(
            l_("Allows administrators to delete multiple messages at once."),
            l_("Useful for cleaning up chat history and removing unwanted content."),
        )
    ),
    filter_actions=get_filter(),
    modern_actions=(DelMsgModernModern,),
)
