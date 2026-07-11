from aiogram.types import InlineKeyboardMarkup

from sophie_bot.modules.notes.utils.buttons.compat import parse_legacy_text_buttons
from sophie_bot.modules.notes.utils.buttons.renderer import render_buttons
from sophie_bot.modules.utils_.legacy_buttons import LEGACY_BUTTON_ACTIONS


def legacy_button_parser(chat_tid: int, texts: str, pm: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    text, buttons = parse_legacy_text_buttons(texts)
    markup = render_buttons(buttons, chat_tid)

    if pm:
        note_payload_prefix = LEGACY_BUTTON_ACTIONS.get("note")
        for row in markup.inline_keyboard:
            for button in row:
                if button.url and note_payload_prefix and note_payload_prefix in button.url:
                    payload = button.url.rsplit("start=", maxsplit=1)[-1]
                    # aiogram's InlineKeyboardButton is a MutableTelegramObject
                    # (frozen=False); ty misreads the frozen-config override and
                    # flags these writable fields as read-only.
                    button.url = None  # ty:ignore[invalid-assignment]
                    button.callback_data = payload  # ty:ignore[invalid-assignment]

    return text, markup
