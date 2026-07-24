from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.captcha_button import CaptchaButton
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.connect_button import ConnectButton
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.delete_button import DeleteButton
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.note_button import NoteButton
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.rules_button import RulesButton
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_dm_button import SophieDMButton
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.url_button import URLButton

if TYPE_CHECKING:
    from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_button_abc import SophieButtonABC


class ButtonDefinition(NamedTuple):
    action: ButtonAction
    button_class: type[SophieButtonABC]


BUTTON_DEFINITIONS = [
    ButtonDefinition(ButtonAction.url, URLButton),
    ButtonDefinition(ButtonAction.note, NoteButton),
    ButtonDefinition(ButtonAction.rules, RulesButton),
    ButtonDefinition(ButtonAction.delmsg, DeleteButton),
    ButtonDefinition(ButtonAction.connect, ConnectButton),
    ButtonDefinition(ButtonAction.captcha, CaptchaButton),
    ButtonDefinition(ButtonAction.sophiedm, SophieDMButton),
]

ALL_BUTTONS: list[SophieButtonABC] = [d.button_class() for d in BUTTON_DEFINITIONS]

ASS_MAPPING: dict[str, ButtonAction] = {
    ass_type: d.action for d in BUTTON_DEFINITIONS for ass_type in d.button_class.button_type_names
}
