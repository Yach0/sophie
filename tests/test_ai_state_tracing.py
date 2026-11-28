from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sentry_sdk
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages_between
from sophie_bot.modules.ai.utils.chatbot_tool_history import get_tool_exchanges


class _TraceTransport(Transport):
    def __init__(self, spans: list[dict[str, Any]]) -> None:
        super().__init__()
        self.spans = spans

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.headers.get("type") == "span" and item.payload.json is not None:
                self.spans.extend(item.payload.json["items"])

    def flush(self, *_args: Any, **_kwargs: Any) -> None:
        """Flush pending events."""

    def kill(self) -> None:
        """Kill transport."""


class _CacheRedis:
    def __init__(self, message: MessageType, tool_payload: bytes) -> None:
        self.message = message
        self.tool_payload = tool_payload
        self.fail_reads = False

    async def zrangebyscore(self, _key: str, minimum: float, maximum: float) -> list[str]:
        if self.fail_reads:
            raise RuntimeError("cache unavailable")
        return [self.message.model_dump_json()] if minimum <= self.message.created_at.timestamp() <= maximum else []

    async def hgetall(self, _key: str) -> dict[bytes, bytes]:
        return {b"99": self.tool_payload}


@pytest.fixture
def trace_events() -> Iterator[list[dict[str, Any]]]:
    spans: list[dict[str, Any]] = []
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=_TraceTransport(spans),
        traces_sample_rate=1.0,
        default_integrations=False,
    )
    client = sentry_sdk.get_client()
    try:
        yield spans
    finally:
        client.close()
        sentry_sdk.get_global_scope().set_client(None)


@pytest.fixture
def cache() -> _CacheRedis:
    message = MessageType(
        user_id=111,
        message_id=99,
        text="sensitive cached chat message",
        created_at=datetime(2026, 9, 28, tzinfo=UTC),
        username="private_name",
    )
    exchange = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="research", args={"secret": "private search"}, tool_call_id="private-id")]
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="research", content="sensitive tool result", tool_call_id="private-id")]
        ),
    ]
    return _CacheRedis(message, ModelMessagesTypeAdapter.dump_json(exchange))


@pytest.mark.asyncio
async def test_cache_spans_are_children_of_active_trace_with_private_payload_excluded(
    trace_events: list[dict[str, Any]], cache: _CacheRedis
) -> None:
    with sentry_sdk.start_span(
        name="ai request", attributes={"sentry.op": "bot.update"}, parent_span=None
    ) as root_span:
        messages = await get_cached_messages_between(
            123456,
            cache.message.created_at - timedelta(minutes=1),
            cache.message.created_at,
            redis=cache,  # type: ignore[arg-type]
        )
        exchanges = await get_tool_exchanges(123456, redis=cache)  # type: ignore[arg-type]
        assert messages == (cache.message,)
        assert len(exchanges[99]) == 2

    sentry_sdk.flush()
    assert len(trace_events) == 3
    spans = {span["name"]: span for span in trace_events if not span["is_segment"]}
    assert set(spans) == {"Read cached messages", "Read tool history"}
    assert {span["parent_span_id"] for span in spans.values()} == {root_span.span_id}
    assert {span["trace_id"] for span in spans.values()} == {root_span.trace_id}
    message_attrs = spans["Read cached messages"]["attributes"]
    history_attrs = spans["Read tool history"]["attributes"]
    assert message_attrs["ai.cache.hit"]["value"] is True
    assert message_attrs["ai.cache.message_count"]["value"] == 1
    assert history_attrs["ai.cache.hit"]["value"] is True
    assert history_attrs["ai.cache.run_count"]["value"] == 1
    assert message_attrs["sentry.op"]["value"] == history_attrs["sentry.op"]["value"] == "ai.cache"
    assert "sensitive" not in str(trace_events)
    assert "private-id" not in str(trace_events)
    assert "123456" not in str(trace_events)


@pytest.mark.asyncio
async def test_failed_cache_read_retains_exception_and_marks_child_span_failed(
    trace_events: list[dict[str, Any]], cache: _CacheRedis
) -> None:
    cache.fail_reads = True
    with (
        sentry_sdk.start_span(name="ai request", attributes={"sentry.op": "bot.update"}, parent_span=None) as root_span,
        pytest.raises(RuntimeError, match="cache unavailable"),
    ):
        await get_cached_messages_between(
            123456,
            cache.message.created_at - timedelta(minutes=1),
            cache.message.created_at,
            redis=cache,  # type: ignore[arg-type]
        )

    sentry_sdk.flush()
    (child,) = [span for span in trace_events if not span["is_segment"]]
    assert child["parent_span_id"] == root_span.span_id
    assert child["status"] == "error"


@pytest.mark.asyncio
async def test_unsampled_trace_keeps_cache_behavior_without_recording_spans(cache: _CacheRedis) -> None:
    spans: list[dict[str, Any]] = []
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=_TraceTransport(spans),
        traces_sample_rate=0.0,
        default_integrations=False,
    )
    try:
        with sentry_sdk.start_span(name="ai request", attributes={"sentry.op": "bot.update"}, parent_span=None):
            messages = await get_cached_messages_between(
                123456,
                cache.message.created_at - timedelta(minutes=1),
                cache.message.created_at,
                redis=cache,  # type: ignore[arg-type]
            )
            assert messages == (cache.message,)
        sentry_sdk.flush()
        assert spans == []
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)
