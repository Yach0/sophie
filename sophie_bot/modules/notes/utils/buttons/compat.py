from __future__ import annotations

import html
from re import findall, sub

from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons.models import ButtonLayout

LEGACY_BUTTON_PATTERN = r"\[(.+?)\]\((button|btn|#)(.+?)(:.+?|)(:same|)\)(\n|)"

LEGACY_ACTIONS: dict[str, ButtonAction] = {
    "url": ButtonAction.url,
    "sophieurl": ButtonAction.sophiedm,
    "rules": ButtonAction.rules,
    "delmsg": ButtonAction.delmsg,
    "connect": ButtonAction.connect,
    "welcomesecurity": ButtonAction.captcha,
    "note": ButtonAction.note,
    "#": ButtonAction.note,
}


def _get_legacy_argument(raw_button: tuple[str, str, str, str, str, str], action: str) -> str | None:
    raw_argument = raw_button[3]
    if raw_argument:
        argument = raw_argument[1:].replace("`", "")
        if action != "url":
            return argument.lower()
        if argument.startswith("//"):
            return argument[2:]
        return argument

    if action == "#":
        return raw_button[2]

    return None


def parse_legacy_text_buttons(text: str) -> tuple[str, ButtonLayout]:
    raw_buttons = findall(LEGACY_BUTTON_PATTERN, text)
    clean_text = sub(LEGACY_BUTTON_PATTERN, "", text)
    layout = ButtonLayout()
    current_row: list[Button] = []

    for raw_button in raw_buttons:
        title = html.unescape(raw_button[0])
        action = raw_button[1] if raw_button[1] not in ("button", "btn") else raw_button[2]
        button_action = LEGACY_ACTIONS.get(action)

        if button_action is None:
            argument = _get_legacy_argument(raw_button, action)
            if argument:
                clean_text += f"\n[{title}].(btn{action}:{argument})"
            else:
                clean_text += f"\n[{title}].(btn{action})"
            continue

        button = Button(text=title, action=button_action, data=_get_legacy_argument(raw_button, action))

        if raw_button[4] and current_row:
            current_row.append(button)
        else:
            if current_row:
                layout.append(current_row)
            current_row = [button]

    if current_row:
        layout.append(current_row)

    if not clean_text or clean_text.isspace():
        clean_text = ""

    return clean_text, layout
