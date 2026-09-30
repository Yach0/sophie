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
    def __init__(self, transactions: list[dict[str, Any]]) -> None:
        super().__init__()
        self.transactions = transactions

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.headers.get("type") == "transaction" and item.payload.json is not None:
                self.transactions.append(item.payload.json)

    def flush(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def kill(self) -> None:
        pass


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
    transactions: list[dict[str, Any]] = []
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=_TraceTransport(transactions),
        traces_sample_rate=1.0,
        default_integrations=False,
    )
    yield transactions
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
        ModelRequest(parts=[ToolReturnPart(tool_name="research", content="sensitive tool result", tool_call_id="private-id")]),
    ]
    return _CacheRedis(message, ModelMessagesTypeAdapter.dump_json(exchange))


@pytest.mark.asyncio
async def test_cache_spans_are_children_of_active_trace_with_private_payload_excluded(
    trace_events: list[dict[str, Any]], cache: _CacheRedis
) -> None:
    with sentry_sdk.start_transaction(op="bot.update", name="ai request") as transaction:
        messages = await get_cached_messages_between(
            123456, cache.message.created_at - timedelta(minutes=1), cache.message.created_at, redis=cache  # type: ignore[arg-type]
        )
        exchanges = await get_tool_exchanges(123456, redis=cache)  # type: ignore[arg-type]
        assert messages == (cache.message,)
        assert len(exchanges[99]) == 2

    assert len(trace_events) == 1
    event = trace_events[0]
    spans = {span["description"]: span for span in event["spans"]}
    assert set(spans) == {"Read cached messages", "Read tool history"}
    assert {span["parent_span_id"] for span in spans.values()} == {transaction.span_id}
    assert {span["trace_id"] for span in spans.values()} == {transaction.trace_id}
    assert spans["Read cached messages"]["data"]["ai.cache.hit"] is True
    assert spans["Read cached messages"]["data"]["ai.cache.message_count"] == 1
    assert spans["Read tool history"]["data"]["ai.cache.hit"] is True
    assert spans["Read tool history"]["data"]["ai.cache.run_count"] == 1
    assert spans["Read cached messages"]["op"] == spans["Read tool history"]["op"] == "ai.cache"
    assert "sensitive" not in str(event)
    assert "private-id" not in str(event)
    assert "123456" not in str(event)


@pytest.mark.asyncio
async def test_failed_cache_read_retains_exception_and_marks_child_span_failed(
    trace_events: list[dict[str, Any]], cache: _CacheRedis
) -> None:
    cache.fail_reads = True
    with (
        sentry_sdk.start_transaction(op="bot.update", name="ai request") as transaction,
        pytest.raises(RuntimeError, match="cache unavailable"),
    ):
        await get_cached_messages_between(
            123456, cache.message.created_at - timedelta(minutes=1), cache.message.created_at, redis=cache  # type: ignore[arg-type]
        )

    assert trace_events[0]["spans"][0]["parent_span_id"] == transaction.span_id
    assert trace_events[0]["spans"][0]["status"] == "internal_error"


@pytest.mark.asyncio
async def test_unsampled_trace_keeps_cache_behavior_without_recording_spans(cache: _CacheRedis) -> None:
    transactions: list[dict[str, Any]] = []
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=_TraceTransport(transactions),
        traces_sample_rate=0.0,
        default_integrations=False,
    )
    try:
        with sentry_sdk.start_transaction(op="bot.update", name="ai request"):
            messages = await get_cached_messages_between(
                123456, cache.message.created_at - timedelta(minutes=1), cache.message.created_at, redis=cache  # type: ignore[arg-type]
            )
            assert messages == (cache.message,)
        assert transactions == []
    finally:
        sentry_sdk.get_global_scope().set_client(None)
