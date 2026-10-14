from __future__ import annotations

from typing import Any, cast

from aiogram.types import Message, ReplyParameters
from stfu_tg.doc import Element

from sophie_bot.modules.utils_.delivery import deliver_doc


async def reply_or_answer(message: Message, text: Element | str, **kwargs: Any) -> Message:
    """Reply with a complete document, retrying without a deleted reply target."""
    kwargs.setdefault("reply_parameters", ReplyParameters(message_id=message.message_id))
    kwargs.setdefault("message_thread_id", message.message_thread_id)
    return cast(Message, await deliver_doc(message.bot, message.chat.id, text, allow_deleted_reply=True, **kwargs))
