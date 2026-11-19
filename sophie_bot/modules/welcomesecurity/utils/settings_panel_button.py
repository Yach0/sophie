from __future__ import annotations

from aiogram.types import InlineKeyboardButton
from beanie import PydanticObjectId

from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB


def _button(chat_iid: PydanticObjectId, text: str, action: str, value: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=WelcomeSecuritySettingsCB(chat_iid=str(chat_iid), action=action, value=value).pack(),
    )
