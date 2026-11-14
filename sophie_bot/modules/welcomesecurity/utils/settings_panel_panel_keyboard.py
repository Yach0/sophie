from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from beanie import PydanticObjectId

from sophie_bot.db.models import GreetingsModel
from sophie_bot.modules.welcomesecurity.utils.settings_panel_button import _button
from sophie_bot.utils.i18n import gettext as _


def _panel_keyboard(settings: GreetingsModel, chat_iid: PydanticObjectId) -> InlineKeyboardMarkup:
    captcha_enabled = bool(settings.welcome_security and settings.welcome_security.enabled)
    media_enabled = bool(settings.welcome_mute and settings.welcome_mute.enabled and settings.welcome_mute.time)
    cleanup_enabled = bool(settings.clean_service and settings.clean_service.enabled)
    welcome_cleanup_enabled = bool(settings.clean_welcome and settings.clean_welcome.enabled)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    chat_iid,
                    _("Disable CAPTCHA") if captcha_enabled else _("Enable CAPTCHA"),
                    "captcha",
                    int(not captcha_enabled),
                ),
                _button(chat_iid, _("CAPTCHA expiry"), "expiry"),
            ],
            [
                _button(
                    chat_iid,
                    _("Disable media restriction") if media_enabled else _("Enable media restriction"),
                    "media",
                    int(not media_enabled),
                ),
                _button(chat_iid, _("Restriction duration"), "duration"),
            ],
            [
                _button(chat_iid, _("View security message"), "message"),
                _button(chat_iid, _("Edit security message"), "edit"),
            ],
            [_button(chat_iid, _("Reset security message"), "reset_confirm")],
            [
                _button(
                    chat_iid,
                    _("Keep join/leave messages") if cleanup_enabled else _("Delete join/leave messages"),
                    "cleanup",
                    int(not cleanup_enabled),
                )
            ],
            [
                _button(
                    chat_iid,
                    _("Keep previous welcome messages")
                    if welcome_cleanup_enabled
                    else _("Delete previous welcome messages"),
                    "welcome_cleanup",
                    int(not welcome_cleanup_enabled),
                )
            ],
            [_button(chat_iid, _("Pending users"), "pending")],
        ]
    )
