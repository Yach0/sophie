from __future__ import annotations

from aiogram import flags
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from ass_tg.types import EqualsArg, OptionalArg

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.telegram_exceptions import NOT_ENOUGH_RIGHTS
from sophie_bot.services.bot import bot
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Pins replied message"))
class PinHandler(SophieMessageHandler):
    @staticmethod
    def filters():
        return (
            CMDFilter(("pin",)),
            UserRestricting(can_pin_messages=True),
            BotHasPermissions(can_pin_messages=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict):
        return {
            "loud": OptionalArg(EqualsArg("loud")),
            "notify": OptionalArg(EqualsArg("notify")),
        }

    async def handle(self):
        message = self.event
        if not message.reply_to_message:
            await message.reply(_("You need to reply to a message to pin it."))
            return

        # Determine notification (silent by default unless loud/notify is used)
        loud = self.data["loud"] or self.data["notify"]
        disable_notification = not loud

        try:
            await bot.pin_chat_message(
                chat_id=message.chat.id,
                message_id=message.reply_to_message.message_id,
                disable_notification=disable_notification,
            )
        except TelegramBadRequest as err:
            if NOT_ENOUGH_RIGHTS in err.message:
                await message.reply(_("I don't have enough rights to pin messages in this chat."))
            else:
                raise
