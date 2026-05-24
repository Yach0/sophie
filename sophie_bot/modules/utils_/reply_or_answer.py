from __future__ import annotations

from typing import Any

from aiogram.types import Message

from sophie_bot.modules.utils_.common_try import common_try


async def reply_or_answer(message: Message, text: Any, **kwargs: Any) -> Any:
    """Reply to a message, falling back to answer if the reply target is gone."""
    await common_try(
        message.reply(str(text), **kwargs),
        reply_not_found=lambda: message.answer(str(text), **kwargs),
    )
