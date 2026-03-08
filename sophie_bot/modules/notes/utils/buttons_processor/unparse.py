from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sophie_bot.config import CONFIG
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes_buttons import Button, ButtonStyle


def create_inline_button(
    *, text: str, style: ButtonStyle | None = None, url: str | None = None, callback_data: str | None = None
) -> InlineKeyboardButton:
    if style:
        return InlineKeyboardButton(text=text, url=url, callback_data=callback_data, style=style)

    return InlineKeyboardButton(text=text, url=url, callback_data=callback_data)


def unparse_button(button: Button, chat_id: int) -> InlineKeyboardButton:
    action = button.action
    text = button.text
    data = button.data

    if action == ButtonAction.url:
        return create_inline_button(text=text, url=data, style=button.style)

    elif action == ButtonAction.sophiedm:
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}", style=button.style)

    elif action == ButtonAction.rules:
        cb = "btn_rules"
        string = f"{cb}_{chat_id}"
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}?start={string}", style=button.style)

    elif action == ButtonAction.delmsg:
        cb = "btn_deletemsg_cb"
        string = f"{cb}_{chat_id}"
        return create_inline_button(text=text, callback_data=string, style=button.style)

    elif action == ButtonAction.connect:
        cb = "btn_connect_start"
        string = f"{cb}_{chat_id}"
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}?start={string}", style=button.style)

    elif action == ButtonAction.captcha:
        cb = "btnwelcomesecuritystart"
        string = f"{cb}_{chat_id}"
        return create_inline_button(text=text, url=f"https://t.me/{CONFIG.username}?start={string}", style=button.style)

    elif action == ButtonAction.note:
        cb = "btnnotesm"
        string = f"{cb}_{data}_{chat_id}" if data else f"{cb}_{chat_id}"
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
