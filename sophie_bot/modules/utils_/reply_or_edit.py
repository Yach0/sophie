from aiogram.types import CallbackQuery, InaccessibleMessage, InputRichMessage, Message
from stfu_tg.doc import Element

from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _


def _accessible_message(event: Message | CallbackQuery) -> Message | None:
    if isinstance(event, CallbackQuery):
        if isinstance(event.message, InaccessibleMessage):
            raise SophieException(_("The message is inaccessible. Please write the command again"))
        return event.message
    return None


async def reply_or_edit(event: Message | CallbackQuery, text: Element | str, **kwargs):
    rendered_text = str(text)

    if (edit_target := _accessible_message(event)) is not None:
        return await common_try(edit_target.edit_text(text=rendered_text, **kwargs))
    if isinstance(event, Message):
        return await common_try(event.reply(rendered_text, **kwargs))
    raise ValueError("answer: Wrong event type")


async def reply_or_edit_rich(event: Message | CallbackQuery, doc: Element, **kwargs):
    """Reply or edit using Telegram's rich message parser without a silent fallback."""
    edit_target = _accessible_message(event)
    rich_message = InputRichMessage(html=doc.to_rich())
    if edit_target is not None:
        return await bot.edit_message_text(
            chat_id=edit_target.chat.id,
            message_id=edit_target.message_id,
            rich_message=rich_message,
            **kwargs,
        )
    if isinstance(event, Message):
        return await bot.send_rich_message(
            chat_id=event.chat.id,
            message_thread_id=event.message_thread_id,
            rich_message=rich_message,
            **kwargs,
        )
    raise ValueError("reply_or_edit_rich: Wrong event type")
