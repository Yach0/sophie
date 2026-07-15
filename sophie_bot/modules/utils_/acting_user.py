from __future__ import annotations

from typing import Any

from aiogram.types import Message

from sophie_bot.db.models import ChatModel
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.utils.i18n import gettext as _


async def require_acting_user(event: Message, data: dict[str, Any]) -> ChatModel | None:
    """Return the acting user's ChatModel, replying and returning None when they can't be identified.

    SaveChatsMiddleware leaves `user_db` as None for admins posting anonymously, because the message
    is sent as the group itself rather than by a user. The key is still present, so `.get()` is not
    enough of a guard, and `message.from_user` is set to GroupAnonymousBot rather than being absent.

    Commands that record or compare who acted need a real identity, so they must say so instead of
    dereferencing None.
    """
    user_db: ChatModel | None = data.get("user_db")

    if user_db is None:
        await reply_or_answer(
            event,
            _("Sophie can't tell who you are while you're posting anonymously. Turn off anonymous mode and try again."),
        )

    return user_db
