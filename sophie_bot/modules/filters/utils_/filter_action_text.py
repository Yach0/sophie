from stfu_tg import Section, VList
from stfu_tg.doc import Element as StfuElement

from sophie_bot.shared.action_registry import ALL_MODERN_ACTIONS
from sophie_bot.shared.actions import ModernActionABC
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _


def get_modern_action_text(action: ModernActionABC) -> str:
    return f"{action.icon} {action.title}"


def filter_action_text(_action: str | None, actions: list[str] | None) -> StfuElement | LazyProxy | str:
    if not actions:
        if _action:
            return _action
        return _("No actions configured")

    if len(actions) == 1:
        return get_modern_action_text(ALL_MODERN_ACTIONS[actions[0]])

    return Section(
        VList(*(get_modern_action_text(ALL_MODERN_ACTIONS[action_name]) for action_name in actions), indent=2)
    )
