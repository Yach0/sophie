from __future__ import annotations

from unittest.mock import patch

from sophie_bot.modules.filters.utils_.filter_action_text import filter_action_text


class DummyModernAction:
    icon = "X"
    title = "Example"


def test_filter_action_text_renders_single_modern_action() -> None:
    with patch(
        "sophie_bot.modules.filters.utils_.filter_action_text.ALL_MODERN_ACTIONS",
        {"example": DummyModernAction()},
    ):
        rendered = filter_action_text(None, ["example"])

    assert str(rendered) == "X Example"


def test_filter_action_text_describes_legacy_filter_without_actions() -> None:
    assert filter_action_text(None, []) == "No actions configured"
