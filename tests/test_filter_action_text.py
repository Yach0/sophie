from __future__ import annotations

from sophie_bot.modules.filters.utils_.filter_action_text import filter_action_text
from sophie_bot.shared.actions import ActionDefinition

ACTIONS = {
    "example": ActionDefinition[None](name="example", icon="X", title="Example")
}


def test_filter_action_text_renders_single_modern_action() -> None:
    rendered = filter_action_text(None, ["example"], ACTIONS)
    assert str(rendered) == "X Example"


def test_filter_action_text_renders_legacy_action_without_modern_actions() -> None:
    assert filter_action_text("legacy_action", [], ACTIONS) == "legacy_action"


def test_filter_action_text_describes_empty_legacy_filter_without_actions() -> None:
    assert filter_action_text(None, [], ACTIONS) == "No actions configured"
