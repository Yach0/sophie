from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message as AiogramMessage

from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.legacy_buttons import LEGACY_DELETE_MESSAGE_BUTTON_PREFIX
from sophie_bot.utils.handlers import SophieCallbackQueryHandler


class LegacyDelMsgButton(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (F.data.regexp(f"{LEGACY_DELETE_MESSAGE_BUTTON_PREFIX}_"),)

    async def handle(self) -> Any:
        message = self.event.message
        if not isinstance(message, AiogramMessage):
            return

        await common_try(message.delete())
