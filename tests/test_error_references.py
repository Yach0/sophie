from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sophie_bot.modules.ai.utils import ai_errors
from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, AIRequestFailed, ai_request_failed_message
from sophie_bot.modules.error.handlers import error as error_handler
from sophie_bot.modules.error.utils import capture as error_capture
from sophie_bot.modules.error.utils.error_message import generic_error_message
from sophie_bot.services import logfire as telemetry


@pytest.mark.parametrize(
    ("sentry_event_id", "logfire_trace_id"),
    [
        (None, None),
        ("sentry-event", None),
        (None, "logfire-trace"),
        ("sentry-event", "logfire-trace"),
    ],
)
def test_crash_message_shows_available_references(
    sentry_event_id: str | None, logfire_trace_id: str | None
) -> None:
    message = generic_error_message(
        ValueError("Crash"), sentry_event_id, logfire_trace_id=logfire_trace_id, hide_contact=True
    )["text"]
    assert ("Reference ID" in message) == (sentry_event_id is not None)
    assert ("Trace ID" in message) == (logfire_trace_id is not None)
    assert "Reference IDs" not in message
    if sentry_event_id:
        assert sentry_event_id in message
    if logfire_trace_id:
        assert logfire_trace_id in message


@pytest.mark.parametrize(
    ("sentry_event_id", "logfire_trace_id"),
    [
        ("sentry-event", None),
        (None, "logfire-trace"),
        ("sentry-event", "logfire-trace"),
    ],
)
def test_ai_failure_reuses_captured_references(
    sentry_event_id: str | None, logfire_trace_id: str | None
) -> None:
    failure = AIRequestFailed(sentry_event_id, "The provider failed", logfire_trace_id=logfire_trace_id)
    message = ai_request_failed_message(error=failure)["text"]
    assert ("Reference ID" in message) == (sentry_event_id is not None)
    assert ("Trace ID" in message) == (logfire_trace_id is not None)
    assert "Reference IDs" not in message
    if sentry_event_id:
        assert sentry_event_id in message
    if logfire_trace_id:
        assert logfire_trace_id in message


def test_global_handler_reports_same_crash_to_both_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ValueError("Crash")
    sentry_capture = MagicMock(return_value="sentry-event")
    logfire_capture = MagicMock(return_value="logfire-trace")
    monkeypatch.setattr(error_capture.sentry_sdk, "is_initialized", lambda: True)
    monkeypatch.setattr(error_capture.sentry_sdk, "capture_exception", sentry_capture)
    monkeypatch.setattr(error_handler, "capture_logfire_error", logfire_capture)

    sentry_event_id = error_handler.SophieErrorHandler.capture_sentry(error)
    logfire_trace_id = error_handler.SophieErrorHandler.capture_logfire(error)
    message = generic_error_message(error, sentry_event_id, logfire_trace_id=logfire_trace_id, hide_contact=True)

    sentry_capture.assert_called_once_with(error)
    logfire_capture.assert_called_once_with(error)
    assert "Reference ID" in message["text"] and "Trace ID" in message["text"]
    assert "sentry-event" in message["text"] and "logfire-trace" in message["text"]


def test_ai_terminal_error_preserves_both_ids_without_recapture(monkeypatch: pytest.MonkeyPatch) -> None:
    sentry_capture = MagicMock(return_value="sentry-event")
    logfire_capture = MagicMock(return_value="logfire-trace")
    global_logfire_capture = MagicMock()
    monkeypatch.setattr(ai_errors, "capture_ai_error", sentry_capture)
    monkeypatch.setattr(ai_errors, "capture_logfire_error", logfire_capture)
    monkeypatch.setattr(error_handler, "capture_logfire_error", global_logfire_capture)
    cause = ValueError("Provider failure")

    failure = ai_errors.ai_request_failed_from_error(cause, AIErrorContext(operation="agent"))
    assert error_handler.SophieErrorHandler.capture_sentry(failure) == "sentry-event"
    assert error_handler.SophieErrorHandler.capture_logfire(failure) == "logfire-trace"
    message = ai_request_failed_message(error=failure)["text"]

    sentry_capture.assert_called_once()
    logfire_capture.assert_called_once_with(cause)
    global_logfire_capture.assert_not_called()
    assert "Reference ID" in message and "Trace ID" in message
    assert "sentry-event" in message and "logfire-trace" in message


def test_logfire_reference_is_the_recorded_error_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ValueError("Crash")
    sdk = MagicMock()
    span = sdk.span.return_value.__enter__.return_value
    span.get_span_context.return_value = SimpleNamespace(trace_id=0x1234, is_valid=True)
    monkeypatch.setattr(telemetry, "logfire", sdk)
    assert telemetry.capture_logfire_error(error) is None
    sdk.span.assert_not_called()

    monkeypatch.setattr(telemetry, "_enabled", True)
    assert telemetry.capture_logfire_error(error) == f"{0x1234:032x}"
    span.record_exception.assert_called_once_with(error)
    assert sdk.span.call_args.kwargs["_level"] == "error"
