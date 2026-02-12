from aiogram import Router
from stfu_tg import Doc

from sophie_bot.modules.notes.utils.buttons_processor.legacy import BUTTONS
from sophie_bot.modules.purges.handlers.button import LegacyDelMsgButton
from sophie_bot.modules.purges.handlers.delete import DelMsgCmdHandler
from sophie_bot.modules.purges.handlers.purge import PurgeMessagesHandler
from sophie_bot.modules.purges.magic_handlers.filter import get_filter
from sophie_bot.modules.purges.magic_handlers.modern_filter import DelMsgModernModern
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

__module_name__ = l_("Purges")
__module_emoji__ = "🗑"
__module_description__ = l_("Delete messages in bulk")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_("Allows administrators to delete multiple messages at once."),
        l_("Useful for cleaning up chat history and removing unwanted content."),
    )
)

__filters__ = get_filter()
__modern_actions__ = (DelMsgModernModern,)

BUTTONS.update({"delmsg": "btn_deletemsg_cb"})

router = Router(name="purges")

__handlers__ = (
    DelMsgCmdHandler,
    PurgeMessagesHandler,
    LegacyDelMsgButton,
)
