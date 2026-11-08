from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from beanie import PydanticObjectId

from sophie_bot.modules.welcomesecurity.utils.settings_panel_button import _button
from sophie_bot.utils.i18n import gettext as _


def _back_keyboard(chat_iid: PydanticObjectId) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button(chat_iid, _("Back to settings"), "home")]])
