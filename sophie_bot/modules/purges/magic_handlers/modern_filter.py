from aiogram.types import Message
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.shared.actions import ActionDefinition, ModernActionABC
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

DELETE_MESSAGE_ACTION = ActionDefinition[None](
    name="delmsg",
    icon="🗑",
    title=l_("Delete the message"),
    allow_warns=True,
    skip_for_admins=True,
)


class DelMsgModern(ModernActionABC[None]):
    definition = DELETE_MESSAGE_ACTION

    @staticmethod
    def description(data: None) -> Element | str:
        return _("Deletes the message")

    async def handle(self, message: Message, data: dict, filter_data: None) -> None:
        if not message.from_user:
            return

        await common_try(message.delete())

        if "filter_id" in data:
            await log_event(
                message.chat.id,
                CONFIG.bot_id,
                LogEvent.MESSAGE_DELETED,
                {
                    "message_id": message.message_id,
                    "filter_id": data["filter_id"],
                    "action": "delmsg",
                },
            )
