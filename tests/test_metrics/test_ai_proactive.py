from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sophie_bot.metrics.ai import (
    track_ai_proactive_action,
    track_ai_proactive_batch,
    track_ai_proactive_event,
)


@pytest.fixture
def mock_config() -> MagicMock:
    config = MagicMock()
    config.metrics_enable = True
    return config


@pytest.fixture(autouse=True)
def setup_config(mock_config: MagicMock):
    with patch("sophie_bot.metrics.ai.CONFIG", mock_config):
        yield


def test_track_ai_proactive_event_counts_sentry_metric() -> None:
    with patch("sophie_bot.metrics.ai.count_metric") as count_metric_mock:
        track_ai_proactive_event("decision_generated", {"feature": "ai_proactive_replies"})

    count_metric_mock.assert_called_once_with(
        "sophie.ai.proactive.events",
        attributes={"event": "decision_generated", "feature": "ai_proactive_replies"},
    )


def test_track_ai_proactive_action_counts_sentry_metric() -> None:
    with patch("sophie_bot.metrics.ai.count_metric") as count_metric_mock:
        track_ai_proactive_action("answer", {"feature": "ai_proactive_replies"})

    count_metric_mock.assert_called_once_with(
        "sophie.ai.proactive.actions",
        attributes={"action": "answer", "feature": "ai_proactive_replies"},
    )


def test_track_ai_proactive_batch_records_distributions() -> None:
    with patch("sophie_bot.metrics.ai.distribution_metric") as distribution_metric_mock:
        track_ai_proactive_batch(15, 2, {"feature": "ai_proactive_replies"})

    assert distribution_metric_mock.call_count == 2
    distribution_metric_mock.assert_any_call(
        "sophie.ai.proactive.batch.messages",
        15,
        attributes={"feature": "ai_proactive_replies"},
    )
    distribution_metric_mock.assert_any_call(
        "sophie.ai.proactive.batch.actions",
        2,
        attributes={"feature": "ai_proactive_replies"},
    )


def test_track_ai_proactive_metrics_respect_metrics_flag(mock_config: MagicMock) -> None:
    mock_config.metrics_enable = False

    with (
        patch("sophie_bot.metrics.ai.count_metric") as count_metric_mock,
        patch("sophie_bot.metrics.ai.distribution_metric") as distribution_metric_mock,
    ):
        track_ai_proactive_event("eligible_message")
        track_ai_proactive_action("none")
        track_ai_proactive_batch(15, 0)

    count_metric_mock.assert_not_called()
    distribution_metric_mock.assert_not_called()
