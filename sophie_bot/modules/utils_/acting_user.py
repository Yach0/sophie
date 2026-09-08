from __future__ import annotations

from aiogram.types import Message

from sophie_bot.db.models import ChatModel
from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.utils.i18n import gettext as _


async def require_acting_user(
    event: Message,
    context: RequestContext,
) -> ChatModel | None:
    """Return the represented actor or explain why anonymous mode cannot proceed."""
    actor = context.actor

    if actor is None:
        await reply_or_answer(
            event,
            _("Sophie can't tell who you are while you're posting anonymously. Turn off anonymous mode and try again."),
        )

    return actor
