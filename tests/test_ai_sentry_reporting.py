from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import sentry_sdk
from beanie import PydanticObjectId
from mistralai.client.errors import SDKError
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    AgentRunError,
    FallbackExceptionGroup,
    ModelHTTPError,
    ToolFailedError,
    ToolRetryError,
    UserError,
)
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from structlog.testing import capture_logs
from tenacity import wait_none

from sophie_bot.db.models import ChatModel, NoteModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.ai.schedules import generate_chat_summaries, write_memory
from sophie_bot.modules.ai.utils import ai_errors, ai_run
from sophie_bot.modules.ai.utils.ai_errors import (
    _DEFAULT_AI_FAILED_MESSAGE,
    AI_PROVIDER_EXCEPTIONS,
    AIErrorContext,
    AIRequestFailed,
    ai_error_scope,
    ai_request_failed_message,
    capture_ai_error,
    ensure_sentry_event_id,
    run_ai_request_with_retries,
)
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.memory_writer import WriterPrompt
from sophie_bot.modules.ai.utils.moderation.providers.base import ModerationUnavailable, run_moderation_request
from sophie_bot.modules.ai.utils.summary_transcript import build_summary_transcript
from sophie_bot.modules.error.handlers.error import SophieErrorHandler
from sophie_bot.modules.notes.schedules import generate_ai_titles
from sophie_bot.services import sentry
from sophie_bot.services.application import ApplicationServices
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


class _TraceCaptureTransport(_CapturingTransport):
    def __init__(self) -> None:
        super().__init__([])
        self.payloads: list[tuple[str | None, dict[str, Any]]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.payload.json is not None:
                self.payloads.append((item.headers.get("type"), item.payload.json))


@pytest.fixture
def instant_retries(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Drop the exponential backoff so retry-exhausting tests do not sleep through it."""
    monkeypatch.setattr(ai_errors, "AI_REQUEST_RETRY_WAIT", wait_none())
    yield


@pytest.fixture
def sentry_events(runtime_sentry_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return runtime_sentry_events


async def _run_candidates(operation: Callable[[str], Awaitable[str]]) -> ai_run.AIAgentResult[str]:
    def model(name: str) -> FunctionModel:
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[TextPart(await operation(name))])

        return FunctionModel(respond, model_name=name)

    primary = model("primary/model")
    backup = model("backup/model")
    plan = AIModelPlan((AIModelCandidate(primary, "primary/model"), AIModelCandidate(backup, "backup/model")))
    return await ai_run.run_ai_text(Agent(primary), "hello", model_plan=plan)


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

    event_id: str | None = None
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
    assert "provider_message" not in event["contexts"]["ai_request"]
    assert "exception" not in event
    # Sanitized events still distinguish models and status codes without provider payloads.
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
    sentry_events: list[dict[str, Any]], instant_retries: None, monkeypatch: pytest.MonkeyPatch
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


async def test_moderation_failure_logs_safe_provider_metadata_and_sentry_reference(
    sentry_events: list[dict[str, Any]],
    instant_retries: None,
) -> None:
    private_input = "private classifier content never belongs in reporting"
    response = httpx.Response(
        429,
        request=httpx.Request(
            "POST",
            "https://api.mistral.ai/v1/chat/moderations",
            content=private_input,
        ),
        text=f'{{"error":{{"message":"backend_out_of_capacity: {private_input}"}}}}',
    )
    context = AIErrorContext(operation="moderation", model_name="mistral-moderation-latest")

    async def exhausted_classifier() -> None:
        raise SDKError(f"Backend overloaded: {private_input}", response)

    with capture_logs() as logs, pytest.raises(ModerationUnavailable):
        await run_moderation_request(exhausted_classifier, context)

    (event,) = sentry_events
    assert event["tags"]["ai.operation"] == "moderation"
    assert event["tags"]["ai.model"] == "mistral-moderation-latest"
    assert event["tags"]["ai.error_type"] == "SDKError"
    assert event["tags"]["ai.status_code"] == "429"
    assert event["fingerprint"] == ["ai-provider-error", "moderation", "mistral-moderation-latest", "SDKError", "429"]
    assert "provider_message" not in event["contexts"]["ai_request"]
    assert "exception" not in event
    assert private_input not in json.dumps(event)
    retry_crumbs = [crumb for crumb in event["breadcrumbs"]["values"] if crumb["category"] == "ai.retry"]
    assert len(retry_crumbs) == ai_errors.AI_REQUEST_RETRY_ATTEMPTS - 1
    assert all("provider_message" not in crumb["data"] for crumb in retry_crumbs)
    assert logs[0]["operation"] == "moderation"
    assert logs[0]["model"] == "mistral-moderation-latest"
    assert logs[0]["error_type"] == "SDKError"
    assert logs[0]["sentry_event_id"] == event["event_id"]
    assert "exc_info" not in logs[0]
    assert private_input not in json.dumps(logs)


async def test_a_rescued_candidate_failure_is_reported_as_a_warning(
    sentry_events: list[dict[str, Any]], instant_retries: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)

    async def operation(active: str) -> str:
        if active == "primary/model":
            raise _model_http_error()
        return "from-backup"

    result = await _run_candidates(operation)
    assert result.output == "from-backup"
    assert result.served_model.model_name == "backup/model"
    # The user got an answer, so nothing else would ever surface that primary/model is failing.
    (event,) = sentry_events
    assert event["level"] == "warning"
    assert event["tags"]["ai.model"] == "primary/model"
    assert event["tags"]["ai.error_type"] == "ModelHTTPError"


async def test_the_failure_that_ends_the_chain_is_attributed_to_the_last_candidate(
    sentry_events: list[dict[str, Any]], instant_retries: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)

    failures = {
        "primary/model": _model_http_error(502),
        "backup/model": _model_http_error(503),
    }

    async def operation(active: str) -> str:
        raise failures[active]

    with pytest.raises(AIRequestFailed) as raised:
        await _run_candidates(operation)

    cause = raised.value.__cause__
    assert isinstance(cause, ExceptionGroup)
    assert cause.exceptions == tuple(failures.values())

    rescue_event, terminal_event = sentry_events
    assert rescue_event["level"] == "warning"
    assert raised.value.sentry_event_id == terminal_event["event_id"]
    assert terminal_event["level"] == "error"
    assert terminal_event["tags"]["ai.model"] == "backup/model"
    assert terminal_event["tags"]["ai.is_fallback"] is True
    assert terminal_event["contexts"]["ai_request"]["primary_model"] == "primary/model"


@pytest.mark.parametrize("status_code", [401, 402, 403])
async def test_a_configuration_error_reaches_the_global_handler_without_walking_the_chain(
    status_code: int, sentry_events: list[dict[str, Any]], instant_retries: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempted: list[str] = []

    async def operation(active: str) -> str:
        attempted.append(active)
        raise ModelHTTPError(status_code=status_code, model_name="whatever", body={"error": {"message": "Invalid key"}})

    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)

    with pytest.raises(ModelHTTPError):
        await _run_candidates(operation)

    assert attempted == ["primary/model"]
    assert sentry_events == []


def test_error_handler_reuses_an_already_captured_event_id(sentry_events: list[dict[str, Any]]) -> None:
    failure = AIRequestFailed("captured-by-the-ai-path")

    assert SophieErrorHandler.capture_sentry(failure) == "captured-by-the-ai-path"
    assert sentry_events == []


def test_error_handler_captures_ai_failure_cause_when_contextual_capture_failed(
    sentry_events: list[dict[str, Any]],
) -> None:
    event_id: str | None = None
    try:
        try:
            raise _model_http_error()
        except ModelHTTPError as error:
            raise AIRequestFailed(None) from error
    except AIRequestFailed as failure:
        event_id = SophieErrorHandler.capture_sentry(failure)

    assert event_id is not None
    assert sentry_events[0]["tags"]["ai.error_type"] == "ModelHTTPError"


def test_error_handler_still_captures_exceptions_nobody_reported(sentry_events: list[dict[str, Any]]) -> None:
    assert SophieErrorHandler.capture_sentry(SophieException("boom")) is not None
    assert len(sentry_events) == 1


def test_ai_provider_exceptions_include_tool_failed_error() -> None:
    assert ToolFailedError in AI_PROVIDER_EXCEPTIONS
    # Control flow and programmer errors must not be routed through provider failover
    assert AgentRunError not in AI_PROVIDER_EXCEPTIONS
    assert ToolRetryError not in AI_PROVIDER_EXCEPTIONS
    assert UserError not in AI_PROVIDER_EXCEPTIONS


@pytest.mark.asyncio
async def test_sentry_agent_trace_includes_model_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _TraceCaptureTransport()
    actual_init = sentry_sdk.init

    def init_with_capture(*args: Any, **kwargs: Any) -> None:
        actual_init(*args, **kwargs, transport=transport)

    monkeypatch.setattr(sentry_sdk, "init", init_with_capture)
    monkeypatch.setattr(sentry.CONFIG, "sentry_url", "https://public@sentry.invalid/1")
    monkeypatch.setattr(sentry.CONFIG, "sentry_traces_sample_rate", 1.0)
    sentry.init_sentry()
    try:
        agent = Agent(TestModel(custom_output_text="telemetry reply"), name="sophie:chat")
        with sentry_sdk.start_transaction(op="bot.update", name="chatbot test"):
            result = await agent.run("telemetry prompt")
        sentry_sdk.flush()

        assert result.output == "telemetry reply"
        payloads = transport.payloads
        serialized = str(payloads)
        assert any(kind in {"transaction", "span"} for kind, _ in payloads)
        assert "sophie:chat" in serialized
        assert "telemetry prompt" in serialized
        assert "telemetry reply" in serialized
    finally:
        sentry_sdk.get_global_scope().set_client(None)


def test_capture_ai_error_includes_user_facing_message_in_context_and_tags(
    sentry_events: list[dict[str, Any]],
) -> None:
    context = AIErrorContext(operation="agent", model_name="openai/gpt-5")
    error = _model_http_error()

    capture_ai_error(error, context)

    (event,) = sentry_events
    expected_message = str(_DEFAULT_AI_FAILED_MESSAGE)
    assert event["tags"]["ai.user_facing_error"] == expected_message
    assert event["contexts"]["ai_request"]["user_facing_message"] == expected_message


async def test_runtime_filter_preserves_unexpected_agent_failure_without_duplicates(
    sentry_events: list[dict[str, Any]],
) -> None:
    failure = RuntimeError("unexpected agent bug")

    async def operation(active: str) -> str:
        raise failure

    with (
        ai_error_scope(AIErrorContext(operation="response", origin="automatic")),
        pytest.raises(RuntimeError) as raised,
    ):
        await _run_candidates(operation)

    assert raised.value is failure
    (event,) = sentry_events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    assert event["tags"]["ai.origin"] == "automatic"
    assert event["tags"]["ai.operation"] == "response"
    assert SophieErrorHandler.capture_sentry(failure) == event["event_id"]
    assert sentry_events == [event]


async def test_runtime_filter_preserves_provider_failure_outside_owned_boundary(
    sentry_events: list[dict[str, Any]],
) -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise _model_http_error(402)

    with pytest.raises(ModelHTTPError):
        await Agent(FunctionModel(respond)).run("private request")

    (event,) = sentry_events
    assert event["exception"]["values"][-1]["type"] == "ModelHTTPError"


def test_ensure_sentry_event_id_captures_cause_when_missing(
    sentry_events: list[dict[str, Any]],
) -> None:
    cause = _model_http_error()
    failure = AIRequestFailed(None)
    failure.__cause__ = cause

    event_id = ensure_sentry_event_id(failure)

    assert event_id is not None
    assert failure.sentry_event_id == event_id
    assert len(sentry_events) == 1
    assert sentry_events[0]["tags"]["ai.error_type"] == "ModelHTTPError"


def test_ai_request_failed_message_preserves_custom_docs_from_error() -> None:
    custom_text = "The AI request took too long and was cancelled. Please try again."
    failure = AIRequestFailed("test-event-id", custom_text)

    message = ai_request_failed_message(error=failure)

    assert custom_text in message["text"]
    assert "Reference ID" in message["text"]
    assert "test-event-id" in message["text"]


def test_automatic_failure_is_reported_without_claiming_public_delivery(
    sentry_events: list[dict[str, Any]],
) -> None:
    capture_ai_error(
        _model_http_error(402),
        AIErrorContext(operation="agent", model_name="openai/gpt-5", origin="automatic"),
    )
    assert sentry_events
    event = sentry_events[-1]
    assert event["contexts"]["ai_request"]["origin"] == "automatic"
    assert "user_facing_message" not in event["contexts"]["ai_request"]
    assert "ai.user_facing_error" not in event["tags"]


def test_terminal_failure_does_not_reuse_a_warning_reference(sentry_events: list[dict[str, Any]]) -> None:
    failure = _model_http_error()
    context = AIErrorContext(operation="response")
    warning_id = capture_ai_error(failure, context, level="warning")
    terminal_id = capture_ai_error(failure, context)
    assert warning_id != terminal_id
    assert [event["level"] for event in sentry_events] == ["warning", "error"]
    assert SophieErrorHandler.capture_sentry(failure) == terminal_id
    assert len(sentry_events) == 2


async def test_nested_agents_report_unexpected_failure_once(sentry_events: list[dict[str, Any]]) -> None:
    failure = RuntimeError("nested agent bug")

    async def inner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise failure

    async def outer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return await Agent(FunctionModel(inner)).run("inner")

    with pytest.raises(RuntimeError), ai_error_scope(AIErrorContext(operation="response")):
        await Agent(FunctionModel(outer)).run("outer")
    assert len(sentry_events) == 1
    assert SophieErrorHandler.capture_sentry(failure) == sentry_events[0]["event_id"]


def test_global_provider_failure_log_does_not_include_private_traceback() -> None:
    try:
        raise ModelHTTPError(402, "model", body="private provider payload")
    except ModelHTTPError as failure:
        with capture_logs() as logs:
            SophieErrorHandler.log_to_console(
                type(failure), failure, failure.__traceback__, sentry_event_id="reference"
            )
    assert len(logs) == 1
    assert logs[0]["sentry_event_id"] == "reference"
    assert "exc_info" not in logs[0]
    assert "private provider payload" not in json.dumps(logs)


async def test_mixed_fallback_group_preserves_unexpected_failure(
    sentry_events: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = FallbackExceptionGroup("mixed failures", [_model_http_error(), ValueError("programming bug")])
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)

    async def operation(active: str) -> str:
        raise failure

    with pytest.raises(FallbackExceptionGroup) as raised:
        await _run_candidates(operation)
    assert raised.value is failure
    (event,) = sentry_events
    assert any(value["type"] == "ValueError" for value in event["exception"]["values"])
    assert SophieErrorHandler.capture_sentry(failure) == event["event_id"]
    assert len(sentry_events) == 1


@pytest.mark.parametrize("operation", ["notes:metadata", "summary:chat", "memory:writer:single"])
@pytest.mark.parametrize("failure_kind", ["provider", "configuration", "unexpected"])
async def test_scheduled_task_entry_points_report_automatic_failures(
    operation: str,
    failure_kind: str,
    test_services: ApplicationServices,
    sentry_events: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = (
        RuntimeError("unexpected task bug")
        if failure_kind == "unexpected"
        else _model_http_error(402 if failure_kind == "configuration" else 503)
    )

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise failure

    model = FunctionModel(respond, model_name="scheduled/model")
    plan = AIModelPlan((AIModelCandidate(model, model.model_name),))
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)
    monkeypatch.setattr(generate_ai_titles, "get_chat_default_model_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(generate_chat_summaries, "get_chat_summary_model_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(generate_chat_summaries, "get_value", AsyncMock(return_value="Summarize discussions"))
    monkeypatch.setattr(generate_chat_summaries, "resolve_chat_service_tier", AsyncMock(return_value=None))
    monkeypatch.setattr(write_memory, "single_model_plan", lambda name: plan)
    chat = ChatModel(
        id=PydanticObjectId(),
        tid=-123,
        type=ChatType.group,
        first_name_or_title="Scheduled tasks",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )

    expected_type = AIRequestFailed if failure_kind == "provider" else type(failure)
    with pytest.raises(expected_type) as raised:
        if operation == "notes:metadata":
            note = NoteModel(chat_id=chat.tid, chat=chat, names=("test",), text="Note content")
            await generate_ai_titles.GenerateAITitles(test_services).generate_data(note, chat.iid, chat.tid)
        elif operation == "summary:chat":
            transcript = build_summary_transcript((), anonymize=True)
            await generate_chat_summaries.GenerateChatSummaries(test_services).generate_summary_groups(
                transcript, chat.iid, chat.tid
            )
        else:
            prompt = WriterPrompt(text="Message batch", batch_ids=frozenset(), messages={}, speakers={}, heads={})
            await write_memory.WriteMemory(test_services)._run_writer(chat, prompt)

    if failure_kind == "provider":
        assert raised.value.__cause__ is failure
    else:
        assert raised.value is failure
    assert ai_errors.is_silent_ai_error(raised.value)
    event_id = SophieErrorHandler.capture_sentry(raised.value)
    (event,) = sentry_events
    assert event_id == event["event_id"]
    assert event["tags"]["ai.origin"] == "automatic"
    assert event["tags"]["ai.operation"] == operation
    assert event["contexts"]["ai_request"]["origin"] == "automatic"
    assert "user_facing_message" not in event["contexts"]["ai_request"]
    assert "ai.user_facing_error" not in event["tags"]
    assert SophieErrorHandler.capture_sentry(raised.value) == event["event_id"]
    assert len(sentry_events) == 1
