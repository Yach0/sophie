from typing import Optional

from aiogram.types import InlineKeyboardMarkup, Message, User

from sophie_bot.db.models import RulesModel
from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.greetings.default_welcome import get_default_welcome_message
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.utils.i18n import gettext as _


async def send_welcome(
    message: Message,
    saveable: Optional[Saveable],
    cleanservice_enabled: bool,
    chat_rules: Optional[RulesModel],
    user: Optional[User] = None,
    send_to_chat_id: Optional[int] = None,
    additional_keyboard: InlineKeyboardMarkup | None = None,
    receiver_user_id: int | None = None,
) -> Message | None:
    chat_id = send_to_chat_id or message.chat.id

    rules_text = chat_rules.text or "" if chat_rules else _("No chat rules, have fun!")
    additional_fillings = {"rules": rules_text}

    saveable = saveable or get_default_welcome_message(bool(chat_rules))

    return await send_saveable(
        message,
        chat_id,
        saveable,
        reply_to=message.message_id if not cleanservice_enabled and send_to_chat_id is None else None,
        additional_fillings=additional_fillings,
        additional_keyboard=additional_keyboard,
        user=user,
        message_thread_id=message.message_thread_id if send_to_chat_id is None else None,
        receiver_user_id=receiver_user_id,
    )
