from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic_ai.models.test import TestModel

from sophie_bot.config import Config
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.chatbot_agent import build_chatbot_agent
from sophie_bot.modules.ai.utils.sophie_inspect import _build_agent
from sophie_bot.services import logfire as telemetry


@pytest.mark.parametrize(
    "environment",
    ["development", "development-beta", "staging", "production"],
)
def test_logfire_token_controls_full_content(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    sdk = MagicMock()
    monkeypatch.setattr(telemetry, "logfire", sdk)
    empty = Config(_env_file=None, logfire_token=" ", environment=environment)
    assert not telemetry.start_logfire(empty)
    sdk.configure.assert_not_called()

    config = Config(_env_file=None, logfire_token="  dummy-token  ", environment=environment)
    try:
        assert telemetry.start_logfire(config)
        assert telemetry.start_logfire(config)
        assert telemetry.logfire_enabled()
        sdk.configure.assert_called_once()
        assert sdk.configure.call_args.kwargs["environment"] == f"{environment}_bot"
        assert sdk.configure.call_args.kwargs["console"] is False
        assert sdk.configure.call_args.kwargs["scrubbing"] is False
        sdk.instrument_pydantic_ai.assert_called_once_with(include_content=True, include_binary_content=True)
        calls = [sdk_call[0] for sdk_call in sdk.mock_calls]
        assert calls.index("configure") < calls.index("instrument_pydantic_ai")
    finally:
        telemetry.stop_logfire()
    sdk.shutdown.assert_called_once_with(flush=True)
    assert not telemetry.logfire_enabled()


def test_logfire_captures_debug_without_enabling_console_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = MagicMock()
    monkeypatch.setattr(telemetry, "logfire", sdk)
    root_logger = telemetry.logging.getLogger()
    previous_level = root_logger.level
    root_logger.setLevel(telemetry.logging.INFO)
    console_handler = telemetry.logging.Handler(level=telemetry.logging.INFO)
    root_logger.addHandler(console_handler)
    try:
        assert telemetry.start_logfire(Config(_env_file=None, logfire_token="dummy-token"))
        telemetry.logging.getLogger("sophie-debug-test").debug("Debug trace")
        sdk.log.assert_called_once()
        assert sdk.log.call_args.args[0] == "debug"
        assert sdk.log.call_args.kwargs["attributes"]["message"] == "Debug trace"
        assert console_handler.level == telemetry.logging.INFO
        telemetry.stop_logfire()
        assert root_logger.level == telemetry.logging.INFO
    finally:
        telemetry.stop_logfire()
        root_logger.removeHandler(console_handler)
        root_logger.setLevel(previous_level)


@pytest.mark.parametrize(
    ("stdlib_level", "logfire_level"),
    [
        (10, "debug"),
        (20, "info"),
        (30, "warn"),
        (40, "error"),
        (50, "fatal"),
    ],
)
def test_application_log_levels(monkeypatch: pytest.MonkeyPatch, stdlib_level: int, logfire_level: str) -> None:
    sdk = MagicMock()
    monkeypatch.setattr(telemetry, "logfire", sdk)
    record = telemetry.logging.LogRecord("sophie-test", stdlib_level, __file__, 0, "Service started", (), None)
    telemetry._ApplicationLogfireHandler().emit(record)
    assert sdk.log.call_args.args[0] == logfire_level


@pytest.mark.parametrize("failed_step", ["configure", "instrument_pydantic_ai"])
def test_logfire_initialization_failure_shuts_down(monkeypatch: pytest.MonkeyPatch, failed_step: str) -> None:
    sdk = MagicMock()
    getattr(sdk, failed_step).side_effect = RuntimeError("initialization failed")
    monkeypatch.setattr(telemetry, "logfire", sdk)
    with pytest.raises(RuntimeError, match="initialization failed"):
        telemetry.start_logfire(Config(_env_file=None, logfire_token="dummy-token"))
    sdk.shutdown.assert_called_once_with(flush=True)
    assert not telemetry.logfire_enabled()


def test_unhandled_error_span_names_the_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ValueError("No AI model in the catalog serves entertainment:translation")
    sdk = MagicMock()
    span = sdk.span.return_value.__enter__.return_value
    span.get_span_context.return_value = None
    monkeypatch.setattr(telemetry, "_enabled", True)
    monkeypatch.setattr(telemetry, "logfire", sdk)

    telemetry.capture_logfire_error(error)

    call = sdk.span.call_args
    assert call.args[0].format(**call.kwargs) == (
        "Error: ValueError: No AI model in the catalog serves entertainment:translation"
    )
    span.record_exception.assert_called_once_with(error)


@pytest.mark.parametrize(
    ("mode", "expected_name"),
    [
        (AIMode.entertainment, "entertainment:chat"),
        (AIMode.support, "support:chat"),
        (AIMode.sophie_help, "sophie_help:chat"),
    ],
)
def test_chat_agent_name_identifies_its_mode(mode: AIMode, expected_name: str) -> None:
    assert build_chatbot_agent(TestModel(), [], mode).name == expected_name


def test_source_inspection_agent_has_its_own_name() -> None:
    assert _build_agent(TestModel()).name == "sophie:source_inspection"
