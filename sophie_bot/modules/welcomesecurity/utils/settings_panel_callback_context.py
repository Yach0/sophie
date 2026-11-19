from __future__ import annotations

from aiogram.types import Message

from sophie_bot.db.models import ChatModel, GreetingsModel
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


async def _callback_context(
    handler: SophieCallbackQueryHandler,
) -> tuple[ChatModel, GreetingsModel, Message] | None:
    chat = handler.connection.db_model
    if handler.connection.type == "private" or handler.callback_data.chat_iid != str(chat.iid):
        await handler.event.answer(
            _("These settings are no longer active. Open /welcome_security again for the intended chat."),
            show_alert=True,
        )
        return None
    await handler.check_for_message()
    message = handler.event.message
    assert isinstance(message, Message)
    settings = await GreetingsModel.get_by_chat_iid(chat.iid)
    return chat, settings, message
