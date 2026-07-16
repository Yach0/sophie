from urllib.parse import urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sophie_bot.config import CONFIG
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes_buttons import Button, ButtonStyle
from sophie_bot.modules.notes.utils.buttons.payloads import (
    LEGACY_CONNECTION_BUTTON_PREFIX,
    LEGACY_DELETE_MESSAGE_BUTTON_PREFIX,
    LEGACY_NOTE_BUTTON_PREFIX,
    LEGACY_RULES_BUTTON_PREFIX,
    LEGACY_WELCOME_SECURITY_BUTTON_PREFIX,
    build_legacy_start_payload,
)
from sophie_bot.utils.logger import log


def _inline_button(
    *, text: str, style: ButtonStyle | None = None, url: str | None = None, callback_data: str | None = None
) -> InlineKeyboardButton:
    if style is None:
        return InlineKeyboardButton(text=text, url=url, callback_data=callback_data)
    return InlineKeyboardButton(text=text, url=url, callback_data=callback_data, style=style)


def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme) and bool(parsed.netloc)
    except (ValueError, TypeError):
        return False


def _telegram_url(payload: str | None = None) -> str:
    base_url = f"https://t.me/{CONFIG.username}"
    return f"{base_url}?start={payload}" if payload else base_url


def render_button(button: Button, chat_tid: int) -> InlineKeyboardButton | None:
    action = button.action
    text = button.text
    data = button.data
    style = button.style

    if action == ButtonAction.url:
        if not data or not _is_valid_url(data):
            log.warning("render_button: skipping invalid URL button", button_text=text, url=data)
            return None
        return _inline_button(text=text, url=data, style=style)

    if action == ButtonAction.delmsg:
        payload = build_legacy_start_payload(LEGACY_DELETE_MESSAGE_BUTTON_PREFIX, chat_tid)
        return _inline_button(text=text, callback_data=payload, style=style)

    if action in {
        ButtonAction.sophiedm,
        ButtonAction.rules,
        ButtonAction.connect,
        ButtonAction.captcha,
        ButtonAction.note,
    }:
        payload_prefixes = {
            ButtonAction.sophiedm: None,
            ButtonAction.rules: LEGACY_RULES_BUTTON_PREFIX,
            ButtonAction.connect: LEGACY_CONNECTION_BUTTON_PREFIX,
            ButtonAction.captcha: LEGACY_WELCOME_SECURITY_BUTTON_PREFIX,
            ButtonAction.note: LEGACY_NOTE_BUTTON_PREFIX,
        }
        payload_prefix = payload_prefixes[action]
        argument = data or "" if action == ButtonAction.note else ""
        payload = None if payload_prefix is None else build_legacy_start_payload(payload_prefix, chat_tid, argument)
        return _inline_button(text=text, url=_telegram_url(payload), style=style)

    return None


def render_buttons(buttons: list[list[Button]], chat_tid: int) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in buttons:
        parsed_row = []
        for button in row:
            parsed_button = render_button(button, chat_tid)
            if parsed_button:
                parsed_row.append(parsed_button)
        if parsed_row:
            keyboard.append(parsed_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
