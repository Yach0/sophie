from __future__ import annotations

import asyncio
import io
import socket

import httpx
import pytest
from bson import Int64, ObjectId

from debug.capture import (
    REDACTED,
    TRACE_CONTEXT,
    TelemetrySink,
    TextCapture,
    build_event,
    new_trace_context,
    normalize_payload,
    set_runtime_ready,
)
from debug.collector import CollectorState, EventStore, create_app
from debug.protocol import (
    TELEMETRY_FRAME_LIMIT,
    CapturedEvent,
    Category,
    EventFrame,
    FrameError,
    Origin,
    Phase,
    read_frame,
    strict_json_loads,
)
from debug.worker import _known_secrets
from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_catalog import AIProviderKind
from sophie_bot.modules.ai.utils import ai_catalog
from sophie_bot.modules.ai.utils.ai_catalog import AICatalog, CatalogProvider


def make_event(summary: str = "ordinary message", payload: object = None) -> CapturedEvent:
    return build_event(
        category=Category.LOG,
        name="test",
        phase=Phase.INSTANT,
        summary=summary,
        payload=payload,
    )


def test_normalization_redacts_without_mutating_source() -> None:
    object_id = ObjectId()
    source = {
        "nested": {"provider_api_key": "secret-value"},
        "uri": "mongodb://user:password@localhost/database",
        "known": "token=999999:known-secret",
        "binary": b"\x00\xff",
        "large_id": Int64(9_007_199_254_740_993),
        "object_id": object_id,
        "text": "ordinary message",
        "token_usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }

    normalized, truncated, redacted = normalize_payload(source, ("999999:known-secret",))

    assert source["nested"]["provider_api_key"] == "secret-value"
    assert source["binary"] == b"\x00\xff"
    assert normalized["nested"]["provider_api_key"] == "[REDACTED]"
    assert normalized["uri"] == "mongodb://[REDACTED]@localhost/database"
    assert normalized["known"] == "token=[REDACTED]"
    assert normalized["binary"] == {"base64": "AP8="}
    assert normalized["large_id"] == {"$numberLong": "9007199254740993"}
    assert normalized["object_id"] == {"$oid": str(object_id)}
    assert normalized["text"] == "ordinary message"
    assert normalized["token_usage"] == {"prompt_tokens": 12, "completion_tokens": 7}
    assert redacted is True
    assert truncated is False


def test_worker_redacts_configured_token_in_unstructured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "token", "999999:private-bot-token")

    normalized, _truncated, redacted = normalize_payload(
        "Telegram call failed for 999999:private-bot-token", _known_secrets()
    )

    assert normalized == "Telegram call failed for [REDACTED]"
    assert redacted is True


def test_worker_redacts_catalog_provider_key_in_unstructured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_key = "999999:private-provider-secret"
    provider = CatalogProvider(name="example", kind=AIProviderKind.openrouter, base_url=None, api_key=provider_key)
    monkeypatch.setattr(ai_catalog, "_catalog", AICatalog(providers={"example": provider}))

    normalized, _truncated, redacted = normalize_payload("Provider error: " + provider_key, _known_secrets())

    assert normalized == "Provider error: [REDACTED]"
    assert redacted is True


def test_text_capture_reconstructs_partial_lines_without_changing_output() -> None:
    original = io.StringIO()
    lines: list[str] = []
    capture = TextCapture(original, lines.append)

    assert capture.write("first") == 5
    assert capture.write(" line\nsecond") == 12
    capture.flush()

    assert original.getvalue() == "first line\nsecond"
    assert lines == ["first line", "second"]


def test_strict_frames_reject_nonfinite_and_unterminated_data() -> None:
    with pytest.raises(FrameError, match="Non-finite"):
        strict_json_loads('{"value":NaN}')
    with pytest.raises(FrameError, match="Non-finite"):
        strict_json_loads('{"value":1e999}')
    with pytest.raises(FrameError, match="Unsafe bare JSON integer"):
        strict_json_loads('{"_id":9007199254740993}')
    assert strict_json_loads('{"_id":{"$numberLong":"9007199254740993"}}') == {
        "_id": {"$numberLong": "9007199254740993"}
    }

    async def scenario() -> None:
        reader = asyncio.StreamReader(limit=TELEMETRY_FRAME_LIMIT)
        reader.feed_data(b'{"protocol_version":1}')
        reader.feed_eof()
        with pytest.raises(FrameError, match="before newline"):
            await read_frame(reader, TELEMETRY_FRAME_LIMIT)

    asyncio.run(scenario())


def test_oversized_payload_keeps_identity_in_truncated_frame() -> None:
    sender, receiver = socket.socketpair()
    receiver.settimeout(2)
    sink = TelemetrySink(sender, "run-id", 42)
    try:
        sink.start()
        sink.emit(make_event(payload={"items": ["x" * 1_000] * 100}))
        encoded = b""
        while not encoded.endswith(b"\n"):
            chunk = receiver.recv(TELEMETRY_FRAME_LIMIT)
            assert chunk, "telemetry sender closed before completing the frame"
            encoded += chunk
    finally:
        sink.close()
        receiver.close()
    assert sink.recorder_errors == 0
    assert sink._pending_bytes == 0
    assert not sink._thread.is_alive()
    assert sender.fileno() == -1

    decoded = EventFrame.model_validate(strict_json_loads(encoded))
    assert decoded.run_id == "run-id"
    assert decoded.pid == 42
    assert decoded.event.truncated is True
    assert decoded.event.payload == {"$truncated": "captured event exceeded the encoded frame limit"}


def test_event_store_reports_eviction_gap_and_omits_list_payload() -> None:
    async def scenario() -> None:
        store = EventStore(max_events=2, max_bytes=1024 * 1024)
        for index in range(4):
            await store.append(
                make_event(summary=f"event-{index}", payload={"index": index}),
                session_id="session",
                run_id="run",
                pid=1,
            )
        page = await store.query(
            after=0,
            before=None,
            limit=200,
            run_id=None,
            category=None,
            trace_id=None,
            chat_tid=None,
            level=None,
            origin=None,
            q=None,
            min_duration_ms=None,
            from_time=None,
            to_time=None,
        )
        stale_page = await store.query(
            after=1,
            before=None,
            limit=200,
            run_id=None,
            category=None,
            trace_id=None,
            chat_tid=None,
            level=None,
            origin=None,
            q=None,
            min_duration_ms=None,
            from_time=None,
            to_time=None,
        )

        assert [event.seq for event in page.events] == [3, 4]
        assert "payload" not in page.events[0].model_dump()
        assert stale_page.gap is True

    asyncio.run(scenario())


def test_trace_context_isolated_across_dispatch_and_child_tasks() -> None:
    async def child_event() -> CapturedEvent:
        await asyncio.sleep(0)
        return make_event()

    async def dispatch(update_id: int) -> tuple[CapturedEvent, CapturedEvent, CapturedEvent]:
        context = new_trace_context(update_id=update_id)
        token = TRACE_CONTEXT.set(context)
        try:
            root = build_event(
                category=Category.TELEGRAM,
                name="incoming_update",
                phase=Phase.START,
                summary="incoming",
                span_id=context.span_id,
                parent_span_id=None,
            )
            foreground = make_event()
            background = await asyncio.create_task(child_event())
            return root, foreground, background
        finally:
            TRACE_CONTEXT.reset(token)

    async def scenario() -> None:
        set_runtime_ready(False)
        assert make_event().origin == Origin.STARTUP
        set_runtime_ready(True)
        assert make_event().origin == Origin.BACKGROUND
        first, second = await asyncio.gather(dispatch(1), dispatch(2))
        assert first[0].trace_id != second[0].trace_id
        for root, foreground, background in (first, second):
            assert root.parent_span_id is None
            assert root.span_id == foreground.parent_span_id
            assert foreground.origin == Origin.UPDATE
            assert background.origin == Origin.BACKGROUND
            assert foreground.trace_id == background.trace_id
        set_runtime_ready(False)

    asyncio.run(scenario())


def test_browser_auth_sets_private_cookie_without_returning_token() -> None:
    async def scenario() -> None:
        state = CollectorState(
            session_id="session",
            bearer_token="bearer-secret",
            browser_credential="cookie-secret",
            csrf_token="csrf-secret",
            api_origin="http://127.0.0.1:8079",
            ui_origin="http://127.0.0.1:5174",
            sanitized_targets={},
        )
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url=state.api_origin, trust_env=False) as client:
            response = await client.post(
                "/api/v1/session/auth",
                headers={"Authorization": "Bearer bearer-secret", "Origin": state.ui_origin},
            )
            assert response.status_code == 204
            assert response.content == b""
            cookie = response.headers["set-cookie"]
            assert "sophie_debug_session=cookie-secret" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=strict" in cookie
            assert "Path=/api/v1" in cookie
            assert "bearer-secret" not in cookie

            rejected = await client.get("/api/v1/session", headers={"Host": "attacker.invalid"})
            assert rejected.status_code == 403

    asyncio.run(scenario())


def test_telemetry_sender_handles_nonblocking_transport_backpressure() -> None:
    sender, receiver = socket.socketpair()
    sender.setblocking(False)
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1_024)
    receiver.settimeout(2)
    sink = TelemetrySink(sender, "run-id", 42)
    for index in range(4):
        sink.emit(make_event(summary=f"event-{index}", payload="x" * 20_000))
    try:
        sink.start()
        encoded = b""
        while encoded.count(b"\n") < 4:
            chunk = receiver.recv(TELEMETRY_FRAME_LIMIT)
            assert chunk
            encoded += chunk
        frames = [EventFrame.model_validate(strict_json_loads(line)) for line in encoded.splitlines()]
        assert [frame.event.summary for frame in frames] == [f"event-{index}" for index in range(4)]
        assert all(frame.event.payload == "x" * 20_000 for frame in frames)
    finally:
        sink.close()
        receiver.close()
    assert sink.recorder_errors == 0
    assert sink._pending_bytes == 0
    assert not sink._thread.is_alive()


def test_telemetry_send_failure_discards_pending_records_and_closes_socket() -> None:
    sender, receiver = socket.socketpair()
    sink = TelemetrySink(sender, "run-id", 42)
    for index in range(3):
        sink.emit(make_event(summary=f"event-{index}"))
    receiver.close()
    try:
        sink.start()
        sink._thread.join(timeout=2)
        assert not sink._thread.is_alive()
        assert sink.recorder_errors == 1
        assert sink.dropped_total == 3
        assert sink._pending_bytes == 0
        assert sink._queue.empty()
        assert sender.fileno() == -1
        sink.emit(make_event())
        assert sink.dropped_total == 4
    finally:
        sink.close()
    sink.close()


def test_telemetry_close_drains_full_queue() -> None:
    sender, receiver = socket.socketpair()
    receiver.settimeout(2)
    sink = TelemetrySink(sender, "run-id", 42, max_records=2)
    sink.emit(make_event(summary="first"))
    sink.emit(make_event(summary="second"))
    try:
        sink.start()
        sink.close()
        encoded = b""
        while chunk := receiver.recv(TELEMETRY_FRAME_LIMIT):
            encoded += chunk
        frames = [EventFrame.model_validate(strict_json_loads(line)) for line in encoded.splitlines()]
        assert [frame.event.summary for frame in frames] == ["first", "second"]
        assert sink.recorder_errors == 0
        assert sink._pending_bytes == 0
        assert not sink._thread.is_alive()
        assert sender.fileno() == -1
    finally:
        sink.close()
        receiver.close()


def test_telemetry_close_interrupts_blocked_sender() -> None:
    sender, receiver = socket.socketpair()
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1_024)
    sink = TelemetrySink(sender, "run-id", 42)
    for _index in range(4):
        sink.emit(make_event(payload="x" * 20_000))
    try:
        sink.start()
        sink.close()
        assert not sink._thread.is_alive()
        assert sink._pending_bytes == 0
        assert sink.recorder_errors == 0
        assert sink.dropped_total > 0
        assert sender.fileno() == -1
    finally:
        sink.close()
        receiver.close()


def test_telemetry_close_before_start_discards_pending_records() -> None:
    sender, receiver = socket.socketpair()
    sink = TelemetrySink(sender, "run-id", 42)
    try:
        sink.emit(make_event())
        sink.close()
        sink.close()
        assert sink._pending_bytes == 0
        assert sink.dropped_total == 1
        assert sender.fileno() == -1
    finally:
        receiver.close()


def test_mapping_keys_preserve_bytes_content_and_distinct_entries() -> None:
    source = {b"first": b"one", b"second": b"two", "first": "string", 1: "integer", 2: "second integer"}
    normalized, truncated, redacted = normalize_payload(source)
    assert normalized == {
        "<bytes:first>": {"base64": "b25l"},
        "<bytes:second>": {"base64": "dHdv"},
        "first": "string",
        "<int:1>": "integer",
        "<int:2>": "second integer",
    }
    assert normalize_payload(source)[0] == normalized
    assert truncated is False
    assert redacted is False


@pytest.mark.parametrize("secret_key", [b"password", b"providerApiKey", memoryview(b"authorization")])
def test_binary_mapping_keys_preserve_secret_redaction(secret_key: bytes | memoryview) -> None:
    source = {secret_key: b"private-value", b"prompt_tokens": 12}
    normalized, truncated, redacted = normalize_payload(source)
    assert REDACTED in normalized.values()
    assert normalized["<bytes:prompt_tokens>"] == 12
    assert source[secret_key] == b"private-value"
    assert truncated is False
    assert redacted is True


def test_mapping_key_collisions_after_encoding_and_redaction_keep_all_values() -> None:
    first_object = object()
    second_object = object()
    source = {
        b"first": 1,
        "<bytes:first>": 2,
        b"\xff": 3,
        b"\\xff": 4,
        first_object: 5,
        second_object: 6,
        "credential-one": 7,
        "credential-two": 8,
    }
    normalized, truncated, redacted = normalize_payload(source, ("credential-one", "credential-two"))
    assert len(normalized) == len(source)
    assert list(normalized.values()) == list(source.values())
    assert normalize_payload(source, ("credential-one", "credential-two"))[0] == normalized
    assert "<bytes:first>#2" in normalized
    assert "[REDACTED]#2" in normalized
    assert truncated is False
    assert redacted is True
