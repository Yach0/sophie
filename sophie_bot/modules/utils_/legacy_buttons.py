from __future__ import annotations

from dataclasses import dataclass

LEGACY_NOTE_BUTTON_PREFIX = "btnnotesm"
LEGACY_RULES_BUTTON_PREFIX = "btn_rules"
LEGACY_WELCOME_SECURITY_BUTTON_PREFIX = "btnwelcomesecuritystart"
LEGACY_DELETE_MESSAGE_BUTTON_PREFIX = "btn_deletemsg_cb"
LEGACY_CONNECTION_BUTTON_PREFIX = "btn_connect_start"
LEGACY_WELCOME_SECURITY_STABLE_PREFIX = "ws_"

LEGACY_NOTE_BUTTON_PATTERN = rf"{LEGACY_NOTE_BUTTON_PREFIX}_(.*)_(.*)"
LEGACY_RULES_BUTTON_PATTERN = rf"{LEGACY_RULES_BUTTON_PREFIX}_(-?\d+)"
LEGACY_WELCOME_SECURITY_BUTTON_PATTERN = rf"{LEGACY_WELCOME_SECURITY_BUTTON_PREFIX}_(-?\d+)"
LEGACY_CONNECTION_BUTTON_PATTERN = rf"(?:connect|{LEGACY_CONNECTION_BUTTON_PREFIX})_(-?\d+)"


@dataclass(frozen=True, slots=True)
class LegacyButtonAction:
    action: str
    payload_prefix: str


LEGACY_BUTTON_ACTIONS: dict[str, str] = {}


def register_legacy_button_actions(*actions: LegacyButtonAction) -> None:
    LEGACY_BUTTON_ACTIONS.update({action.action: action.payload_prefix for action in actions})


def build_legacy_start_payload(payload_prefix: str, chat_tid: int, argument: str = "") -> str:
    if argument:
        return f"{payload_prefix}_{argument}_{chat_tid}"
    return f"{payload_prefix}_{chat_tid}"
