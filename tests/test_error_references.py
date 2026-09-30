from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sophie_bot.modules.ai.utils import ai_errors
from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, AIRequestFailed, ai_request_failed_message
from sophie_bot.modules.error.handlers import error as error_handler
from sophie_bot.modules.error.utils import capture as error_capture
from sophie_bot.modules.error.utils.error_message import generic_error_message


@pytest.mark.parametrize("sentry_event_id", [None, "sentry-event"])
def test_crash_message_shows_sentry_reference(sentry_event_id: str | None) -> None:
    message = generic_error_message(ValueError("Crash"), sentry_event_id, hide_contact=True)["text"]
    assert ("Reference ID" in message) == (sentry_event_id is not None)
    if sentry_event_id:
        assert sentry_event_id in message


@pytest.mark.parametrize("sentry_event_id", [None, "sentry-event"])
def test_ai_failure_shows_sentry_reference(sentry_event_id: str | None) -> None:
    failure = AIRequestFailed(sentry_event_id, "The provider failed")
    message = ai_request_failed_message(error=failure)["text"]
    assert ("Reference ID" in message) == (sentry_event_id is not None)
    if sentry_event_id:
        assert sentry_event_id in message


def test_global_handler_reports_crash_to_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ValueError("Crash")
    sentry_capture = MagicMock(return_value="sentry-event")
    monkeypatch.setattr(error_capture.sentry_sdk, "is_initialized", lambda: True)
    monkeypatch.setattr(error_capture.sentry_sdk, "capture_exception", sentry_capture)

    sentry_event_id = error_handler.SophieErrorHandler.capture_sentry(error)
    message = generic_error_message(error, sentry_event_id, hide_contact=True)

    sentry_capture.assert_called_once_with(error)
    assert "sentry-event" in message["text"]


def test_ai_terminal_error_reuses_sentry_id_without_recapture(monkeypatch: pytest.MonkeyPatch) -> None:
    sentry_capture = MagicMock(return_value="sentry-event")
    monkeypatch.setattr(ai_errors, "capture_ai_error", sentry_capture)
    cause = ValueError("Provider failure")

    failure = ai_errors.ai_request_failed_from_error(cause, AIErrorContext(operation="agent"))
    assert error_handler.SophieErrorHandler.capture_sentry(failure) == "sentry-event"
    message = ai_request_failed_message(error=failure)["text"]

    sentry_capture.assert_called_once()
    assert "sentry-event" in message
