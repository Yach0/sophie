from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from babel.dates import format_timedelta
from beanie import PydanticObjectId

from sophie_bot.db.models import GreetingsModel
from sophie_bot.db.models.greetings import WELCOMEMUTE_DEFAULT_TIME
from sophie_bot.modules.welcomesecurity.handlers.status_overall import CAPTCHA_EXPIRY_OPTIONS, _effective_expiry
from sophie_bot.modules.welcomesecurity.utils.settings_panel_button import _button
from sophie_bot.modules.welcomesecurity.utils.settings_panel_constants import MEDIA_DURATION_OPTIONS
from sophie_bot.utils.i18n import gettext as _


def _duration_keyboard(
    settings: GreetingsModel, chat_iid: PydanticObjectId, locale: str, *, expiry: bool
) -> InlineKeyboardMarkup:
    options = CAPTCHA_EXPIRY_OPTIONS if expiry else MEDIA_DURATION_OPTIONS
    current = (
        _effective_expiry(settings)
        if expiry
        else (settings.welcome_mute.time if settings.welcome_mute else None) or WELCOMEMUTE_DEFAULT_TIME
    )
    buttons = InlineKeyboardBuilder()
    for duration in options:
        label = format_timedelta(duration, locale=locale.replace("-", "_"))
        buttons.add(
            _button(
                chat_iid,
                ("✅ " if duration == current else "") + label,
                "set_expiry" if expiry else "set_duration",
                int(duration.total_seconds()),
            )
        )
    buttons.adjust(2)
    buttons.row(_button(chat_iid, _("Back to settings"), "home"))
    return buttons.as_markup()
