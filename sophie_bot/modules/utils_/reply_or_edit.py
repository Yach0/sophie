from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.types import CallbackQuery, InaccessibleMessage, Message, ReplyParameters
from stfu_tg.doc import Element

from sophie_bot.modules.utils_.delivery import deliver_doc
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _


def _accessible_message(event: Message | CallbackQuery) -> Message | None:
    if isinstance(event, CallbackQuery):
        if isinstance(event.message, InaccessibleMessage):
            raise SophieException(_("The message is inaccessible. Please write the command again"))
        return event.message
    return None


async def reply_or_edit(event: Message | CallbackQuery, text: Element | str, **kwargs: Any) -> Message | bool | None:
    target = _accessible_message(event) or event
    if not isinstance(target, Message):
        raise TypeError("reply_or_edit: Wrong event type")
    kwargs.setdefault("message_thread_id", target.message_thread_id)
    reply_target = (target.reply_to_message or target) if isinstance(event, CallbackQuery) else target
    kwargs.setdefault("reply_parameters", ReplyParameters(message_id=reply_target.message_id))
    return await deliver_doc(
        event.bot,
        target.chat.id,
        text,
        edit_message_id=target.message_id if isinstance(event, CallbackQuery) else None,
        **kwargs,
    )


async def reply_or_edit_rich(
    event: Message | CallbackQuery,
    doc: Element,
    *,
    bot: Bot,
    **kwargs: Any,
) -> Message | bool | None:
    """Reply or edit using Telegram's rich message parser without a silent fallback."""
    target = _accessible_message(event) or event
    if not isinstance(target, Message):
        raise TypeError("reply_or_edit_rich: Wrong event type")
    kwargs.setdefault("message_thread_id", target.message_thread_id)
    reply_target = (target.reply_to_message or target) if isinstance(event, CallbackQuery) else target
    kwargs.setdefault("reply_parameters", ReplyParameters(message_id=reply_target.message_id))
    return await deliver_doc(
        bot,
        target.chat.id,
        doc,
        mode="rich",
        allow_short_rich=True,
        edit_message_id=target.message_id if isinstance(event, CallbackQuery) else None,
        **kwargs,
    )
