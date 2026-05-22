import html
from re import findall, sub

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sophie_bot.config import CONFIG
from sophie_bot.modules.utils_.legacy_buttons import LEGACY_BUTTON_ACTIONS
from sophie_bot.utils.logger import log


def legacy_button_parser(chat_tid: int, texts: str, pm: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    buttons: list[list[InlineKeyboardButton]] = []
    pattern = r"\[(.+?)\]\((button|btn|#)(.+?)(:.+?|)(:same|)\)(\n|)"
    raw_buttons = findall(pattern, texts)
    text = sub(pattern, "", texts)
    btn = None
    for raw_button in raw_buttons:
        name = raw_button[0]

        # Restore escaped name
        name = html.unescape(name)

        action = raw_button[1] if raw_button[1] not in ("button", "btn") else raw_button[2]

        if raw_button[3]:
            argument = raw_button[3][1:].lower().replace("`", "")
        elif action == "#":
            argument = raw_button[2]
            log.debug("legacy_button_parser: hash action", argument=raw_button[2])
        else:
            argument = ""

        if action in LEGACY_BUTTON_ACTIONS:
            cb = LEGACY_BUTTON_ACTIONS[action]
            string = f"{cb}_{argument}_{chat_tid}" if argument else f"{cb}_{chat_tid}"
            start_btn = InlineKeyboardButton(text=name, url=f"https://t.me/{CONFIG.username}?start=" + string)
            cb_btn = InlineKeyboardButton(text=name, callback_data=string)

            if cb.endswith("sm"):
                btn = cb_btn if pm else start_btn
            elif cb.endswith("cb"):
                btn = cb_btn
            elif cb.endswith("start"):
                btn = start_btn
            elif cb.startswith("url"):
                # Workaround to make URLs case-sensitive TODO: make better
                argument = raw_button[3][1:].replace("`", "") if raw_button[3] else ""
                btn = InlineKeyboardButton(text=name, url=argument)
            elif cb.endswith("rules"):
                btn = start_btn
        elif action == "sophieurl":
            btn = InlineKeyboardButton(text=name, url=f"https://t.me/{CONFIG.username}")
        elif action == "url":
            raw_arg = raw_button[3] if raw_button[3] else ""
            argument = raw_arg[1:].replace("`", "")
            if len(argument) >= 2 and argument[0] == "/" and argument[1] == "/":
                argument = argument[2:]
            if argument:
                btn = InlineKeyboardButton(text=name, url=argument)
            else:
                continue
        else:
            # If btn not registred
            btn = None
            if argument:
                text += f"\n[{name}].(btn{action}:{argument})"
            else:
                text += f"\n[{name}].(btn{action})"
                continue

        if btn:
            if raw_button[4] and buttons and len(buttons[-1]) > 0:
                buttons[-1].append(btn)
            else:
                buttons.append([btn])

    if not text or text.isspace():  # TODO: Sometimes we can return text == ' '
        text = ""

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)
