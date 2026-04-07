from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from stfu_tg.doc import Element

from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _


async def reply_or_edit(event: Message | CallbackQuery, text: Element | str, **kwargs):
    rendered_text = str(text)

    if isinstance(event, CallbackQuery) and event.message:
        if isinstance(event.message, InaccessibleMessage):
            raise SophieException(_("The message is inaccessible. Please write the command again"))

        return await common_try(event.message.edit_text(rendered_text, **kwargs))
    if isinstance(event, Message):
        return await common_try(event.reply(rendered_text, **kwargs))
    raise ValueError("answer: Wrong event type")
