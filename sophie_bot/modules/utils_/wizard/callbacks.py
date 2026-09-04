from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class WizardCallback(CallbackData, prefix="wiz", sep=";"):
    """Unified compact callback payload for wizard interactions.

    Fields:
    - scope: Wizard scope identifier (e.g. "antiflood_action", "filter_action", "warn_action_each")
    - op: Operation code (e.g. "home", "add", "select", "configure", "setting", "toggle", "done", "cancel")
    - session_id: Compact identifier binding controls to one rendered wizard session
    - arg: Optional payload argument (e.g. action name, setting identifier, page number)
    """

    scope: str
    op: str
    session_id: str = ""
    arg: str = ""
