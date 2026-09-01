from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
import sentry_sdk
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.test import TestModel
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from tenacity import wait_none

from sophie_bot.modules.ai.middlewares.ai_timeout import AiTimeoutMiddleware
from sophie_bot.modules.ai.utils import ai_errors, ai_run
from sophie_bot.modules.ai.utils.ai_errors import (
    AIErrorContext,
    AIRequestFailed,
    capture_ai_error,
    run_ai_request_with_retries,
)
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate
from sophie_bot.modules.error.handlers.error import SophieErrorHandler
from sophie_bot.utils.exception import SophieException


class _CapturingTransport(Transport):
    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__()
        self._events = events

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.headers.get("type") == "event" and item.payload.json is not None:
                self._events.append(item.payload.json)

    def flush(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def kill(self) -> None:
        return None


@pytest.fixture
def instant_retries(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Drop the exponential backoff so retry-exhausting tests do not sleep through it."""
    monkeypatch.setattr(ai_errors, "AI_REQUEST_RETRY_WAIT", wait_none())
    yield


@pytest.fixture
def sentry_events() -> Iterator[list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=_CapturingTransport(events),
        default_integrations=False,
        environment="test",
    )
    yield events
    sentry_sdk.get_global_scope().set_client(None)


class _NamedModel(TestModel):
    """A ``TestModel`` with a name of its own, so a chain can tell its stand-ins apart."""

    def __init__(self, model_name: str) -> None:
        super().__init__()
        self._name = model_name

    @property
    def model_name(self) -> str:
        return self._name


def _candidate(model_name: str) -> AIModelCandidate:
    return AIModelCandidate(model=_NamedModel(model_name), model_name=model_name)


def _chain(*candidates: AIModelCandidate) -> ai_run.CandidateChain:
    return ai_run.CandidateChain(
        candidates=list(candidates),
        should_try_next=ai_run._should_try_next_model,
        refusal_failover=True,
    )


def _model_http_error(status_code: int = 503) -> ModelHTTPError:
    return ModelHTTPError(
        status_code=status_code,
        model_name="openai/gpt-5",
        body={"error": {"message": "upstream is down"}},
    )


def test_capture_ai_error_flushes_before_returning_reference_id(
    sentry_events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed: list[float] = []
    monkeypatch.setattr(sentry_sdk, "flush", lambda *, timeout: flushed.append(timeout))

    event_id = capture_ai_error(_model_http_error(), AIErrorContext(operation="agent", model_name="openai/gpt-5"))

    assert event_id == sentry_events[0]["event_id"]
    assert flushed == [2.0]


def test_capture_ai_error_tags_and_groups_by_model_and_status(sentry_events: list[dict[str, Any]]) -> None:
    context = AIErrorContext(operation="agent", model_name="openai/gpt-5")

    try:
        raise _model_http_error()
    except ModelHTTPError as error:
        event_id = capture_ai_error(error, context)

    assert event_id is not None
    (event,) = sentry_events
    assert event["event_id"] == event_id
    assert event["level"] == "error"
    assert event["tags"]["ai.operation"] == "agent"
    assert event["tags"]["ai.model"] == "openai/gpt-5"
    assert event["tags"]["ai.status_code"] == "503"
    assert event["tags"]["ai.error_type"] == "ModelHTTPError"
    assert event["tags"]["ai.is_fallback"] is False
    assert event["contexts"]["ai_request"]["provider_message"] == "upstream is down"
    # Without an explicit fingerprint these group by the tenacity frames they surface through,
    # merging every model and status code into one issue.
    assert event["fingerprint"] == ["ai-provider-error", "agent", "openai/gpt-5", "ModelHTTPError", "503"]


def test_capture_ai_error_separates_fingerprints_per_model_and_status(
    sentry_events: list[dict[str, Any]],
) -> None:
    capture_ai_error(_model_http_error(503), AIErrorContext(operation="agent", model_name="openai/gpt-5"))
    capture_ai_error(_model_http_error(429), AIErrorContext(operation="agent", model_name="openai/gpt-5"))
    capture_ai_error(_model_http_error(503), AIErrorContext(operation="stream", model_name="other/model"))

    fingerprints = [event["fingerprint"] for event in sentry_events]
    assert len(fingerprints) == len(set(map(tuple, fingerprints))) == 3


async def test_retries_are_recorded_as_breadcrumbs_on_the_final_event(
    sentry_events: list[dict[str, Any]], instant_retries: None
) -> None:
    context = AIErrorContext(operation="agent", model_name="openai/gpt-5")

    async def always_failing() -> None:
        raise _model_http_error()

    with pytest.raises(ModelHTTPError) as raised:
        await run_ai_request_with_retries(always_failing, context)

    capture_ai_error(raised.value, context)

    (event,) = sentry_events
    retry_crumbs = [crumb for crumb in event["breadcrumbs"]["values"] if crumb["category"] == "ai.retry"]
    assert len(retry_crumbs) == ai_errors.AI_REQUEST_RETRY_ATTEMPTS - 1
    assert retry_crumbs[0]["data"]["status_code"] == 503
    assert f"1/{ai_errors.AI_REQUEST_RETRY_ATTEMPTS}" in retry_crumbs[0]["message"]


async def test_a_rescued_candidate_failure_is_reported_as_a_warning(
    sentry_events: list[dict[str, Any]], instant_retries: None
) -> None:
    primary = _candidate("primary/model")
    backup = _candidate("backup/model")

    async def operation(active: AIModelCandidate) -> str:
        if active is primary:
            raise _model_http_error()
        return "from-backup"

    result, served = await ai_run._run_with_model_candidates(operation, _chain(primary, backup))

    assert (result, served) == ("from-backup", backup)
    # The user got an answer, so nothing else would ever surface that primary/model is failing.
    (event,) = sentry_events
    assert event["level"] == "warning"
    assert event["tags"]["ai.model"] == "primary/model"
    assert event["tags"]["ai.error_type"] == "ModelHTTPError"


async def test_the_failure_that_ends_the_chain_is_attributed_to_the_last_candidate(
    sentry_events: list[dict[str, Any]], instant_retries: None
) -> None:
    primary = _candidate("primary/model")
    backup = _candidate("backup/model")

    async def operation(_active: AIModelCandidate) -> str:
        raise _model_http_error()

    with pytest.raises(AIRequestFailed) as raised:
        await ai_run._run_with_model_candidates(operation, _chain(primary, backup))

    rescue_event, terminal_event = sentry_events
    assert rescue_event["level"] == "warning"
    assert raised.value.sentry_event_id == terminal_event["event_id"]
    assert terminal_event["level"] == "error"
    assert terminal_event["tags"]["ai.model"] == "backup/model"
    assert terminal_event["tags"]["ai.is_fallback"] is True
    assert terminal_event["contexts"]["ai_request"]["primary_model"] == "primary/model"


async def test_a_configuration_error_ends_the_chain_without_walking_it(
    sentry_events: list[dict[str, Any]], instant_retries: None
) -> None:
    attempted: list[str] = []

    async def operation(active: AIModelCandidate) -> str:
        attempted.append(active.model_name)
        raise ModelHTTPError(status_code=401, model_name="whatever", body={"error": {"message": "Invalid key"}})

    with pytest.raises(AIRequestFailed):
        await ai_run._run_with_model_candidates(
            operation, _chain(_candidate("primary/model"), _candidate("backup/model"))
        )

    # Every candidate is refused the same way, so the chain stops and reports exactly one failure.
    assert attempted == ["primary/model"]
    (event,) = sentry_events
    assert event["level"] == "error"
    assert event["tags"]["ai.status_code"] == "401"


async def test_ai_handler_timeout_is_captured_with_a_reference_id(
    sentry_events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sophie_bot.modules.ai.middlewares import ai_timeout

    monkeypatch.setattr(ai_timeout.CONFIG, "ai_timeout_seconds", 0.01)

    async def hanging_handler(_event: Any, _data: dict[str, Any]) -> None:
        await asyncio.sleep(5)

    data = {"handler": SimpleNamespace(flags={"status": "Thinking..."})}

    with pytest.raises(AIRequestFailed) as raised:
        await AiTimeoutMiddleware()(hanging_handler, SimpleNamespace(), data)  # type: ignore[arg-type]

    (event,) = sentry_events
    assert raised.value.sentry_event_id == event["event_id"]
    assert event["tags"]["ai.operation"] == "handler_timeout"
    assert event["tags"]["ai.error_type"] == "TimeoutError"


def test_error_handler_reuses_an_already_captured_event_id(sentry_events: list[dict[str, Any]]) -> None:
    failure = AIRequestFailed("captured-by-the-ai-path")

    assert SophieErrorHandler.capture_sentry(failure) == "captured-by-the-ai-path"
    assert sentry_events == []


def test_error_handler_captures_ai_failure_cause_when_contextual_capture_failed(
    sentry_events: list[dict[str, Any]],
) -> None:
    try:
        try:
            raise _model_http_error()
        except ModelHTTPError as error:
            raise AIRequestFailed(None) from error
    except AIRequestFailed as failure:
        event_id = SophieErrorHandler.capture_sentry(failure)

    assert event_id is not None
    assert sentry_events[0]["exception"]["values"][0]["type"] == "ModelHTTPError"


def test_error_handler_still_captures_exceptions_nobody_reported(sentry_events: list[dict[str, Any]]) -> None:
    assert SophieErrorHandler.capture_sentry(SophieException("boom")) is not None
    assert len(sentry_events) == 1
