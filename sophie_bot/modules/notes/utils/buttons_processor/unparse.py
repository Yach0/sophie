from urllib.parse import urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sophie_bot.config import CONFIG
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes_buttons import Button, ButtonStyle
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_CONNECTION_BUTTON_PREFIX,
    LEGACY_DELETE_MESSAGE_BUTTON_PREFIX,
    LEGACY_NOTE_BUTTON_PREFIX,
    LEGACY_RULES_BUTTON_PREFIX,
    LEGACY_WELCOME_SECURITY_BUTTON_PREFIX,
    build_legacy_start_payload,
)
from sophie_bot.utils.logger import log


def create_inline_button(
    *, text: str, style: ButtonStyle | None = None, url: str | None = None, callback_data: str | None = None
) -> InlineKeyboardButton:
    if style:
        return InlineKeyboardButton(text=text, url=url, callback_data=callback_data, style=style)

    return InlineKeyboardButton(text=text, url=url, callback_data=callback_data)


def _is_valid_url(url: str) -> bool:
    """Check if the URL has a valid scheme and netloc."""
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme) and bool(parsed.netloc)
    except (ValueError, TypeError):
        return False


def unparse_button(button: Button, chat_id: int) -> InlineKeyboardButton | None:
    action = button.action
    text = button.text
    data = button.data

    if action == ButtonAction.url:
        if not data or not _is_valid_url(data):
            log.warning("unparse_button: skipping invalid URL button", button_text=text, url=data)
            return None
        return create_inline_button(text=text, url=data, style=button.style)

    if action == ButtonAction.sophiedm:
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}", style=button.style)

    if action == ButtonAction.rules:
        string = build_legacy_start_payload(LEGACY_RULES_BUTTON_PREFIX, chat_id)
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}?start={string}", style=button.style)

    if action == ButtonAction.delmsg:
        string = build_legacy_start_payload(LEGACY_DELETE_MESSAGE_BUTTON_PREFIX, chat_id)
        return create_inline_button(text=text, callback_data=string, style=button.style)

    if action == ButtonAction.connect:
        string = build_legacy_start_payload(LEGACY_CONNECTION_BUTTON_PREFIX, chat_id)
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}?start={string}", style=button.style)

    if action == ButtonAction.captcha:
        string = build_legacy_start_payload(LEGACY_WELCOME_SECURITY_BUTTON_PREFIX, chat_id)
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}?start={string}", style=button.style)

    if action == ButtonAction.note:
        string = build_legacy_start_payload(LEGACY_NOTE_BUTTON_PREFIX, chat_id, data or "")
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}?start={string}", style=button.style)

    # Fallback for unknown types (should not happen if all covered)
    return create_inline_button(text=text, callback_data="unknown", style=button.style)


def unparse_buttons(buttons: list[list[Button]], chat_id: int) -> InlineKeyboardMarkup:
    keyboard = []
    for row in buttons:
        parsed_row = []
        for button in row:
            parsed_btn = unparse_button(button, chat_id)
            if parsed_btn:
                parsed_row.append(parsed_btn)
        if parsed_row:
            keyboard.append(parsed_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
