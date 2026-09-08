from collections.abc import Mapping
from typing import Any

from stfu_tg import Section, VList
from stfu_tg.doc import Element as StfuElement

from sophie_bot.shared.actions import ActionDefinition
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _


def get_modern_action_text(action: ActionDefinition) -> str:
    return f"{action.icon} {action.title}"


def filter_action_text(
    _action: str | None,
    action_names: list[str] | None,
    actions: Mapping[str, ActionDefinition[Any]],
) -> StfuElement | LazyProxy | str:
    if not action_names:
        if _action:
            return _action
        return _("No actions configured")

    if len(action_names) == 1:
        return get_modern_action_text(actions[action_names[0]])

    return Section(
        VList(
            *(get_modern_action_text(actions[action_name]) for action_name in action_names),
            indent=2,
        )
    )
