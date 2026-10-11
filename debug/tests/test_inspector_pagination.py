from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import JsonValue

from debug.actions import WorkerActionError, WorkerActions
from debug.capture import TelemetrySink
from debug.collector import AiCacheResponse, CollectorState, RedisQueryResponse, create_app
from debug.protocol import ReplyFrame
from sophie_bot.modules.ai.utils.cache_messages import MessageType
from sophie_bot.runtime import BotModeRuntime


def make_actions(redis: object) -> WorkerActions:
    runtime = SimpleNamespace(
        config=SimpleNamespace(model_dump=lambda *, mode: {}),
        services=SimpleNamespace(redis=redis),
    )
    return WorkerActions(cast(BotModeRuntime, runtime), cast(TelemetrySink, SimpleNamespace()))


def row_values(rows: list[JsonValue], field_name: str) -> list[JsonValue]:
    values: list[JsonValue] = []
    for row in rows:
        assert isinstance(row, dict)
        values.append(row[field_name])
    return values


@pytest.mark.parametrize("operation", ["scan", "hash", "set"])
def test_redis_cursor_pages_preserve_oversized_count_batches(operation: str) -> None:
    async def scenario() -> None:
        first_rows: dict[bytes, bytes] | list[bytes]
        last_rows: dict[bytes, bytes] | list[bytes]
        if operation == "hash":
            first_rows = {f"field-{index}".encode(): f"value-{index}".encode() for index in range(600)}
            last_rows = {b"last-field": b"last-value"}
            expected: list[JsonValue] = [[field.decode(), value.decode()] for field, value in first_rows.items()]
            expected_last: list[JsonValue] = [["last-field", "last-value"]]
        else:
            first_rows = [f"item-{index}".encode() for index in range(1_200)]
            last_rows = [b"last-item"]
            expected = [row.decode() for row in first_rows]
            expected_last = ["last-item"]
        scan = AsyncMock(side_effect=[(37, first_rows), (0, last_rows)])
        redis = SimpleNamespace(
            scan=scan,
            hscan=scan,
            sscan=scan,
            type=AsyncMock(return_value=operation.encode()),
            ttl=AsyncMock(return_value=60),
        )
        actions = make_actions(redis)
        payload: dict[str, JsonValue] = {"op": operation, "key": "debug:key", "cursor": 0, "limit": 2}
        first = await actions.execute("redis.query", payload)
        assert isinstance(first, dict)
        RedisQueryResponse.model_validate({**first, "run_id": "run-1"})
        assert first["data"] == expected
        assert first["next_cursor"] == 37
        assert first["has_more"] is True
        assert first["truncated"] is False

        last = await actions.execute("redis.query", {**payload, "cursor": first["next_cursor"]})
        assert isinstance(last, dict)
        assert last["data"] == expected_last
        assert last["next_cursor"] == 0
        assert last["has_more"] is False
        assert last["truncated"] is False

    asyncio.run(scenario())


def test_tool_cursor_pages_preserve_valid_and_invalid_exchanges() -> None:
    async def scenario() -> None:
        rows = {str(index).encode(): b"[]" for index in range(180)}
        rows.update({f"invalid-{index}".encode(): b"not-json" for index in range(180)})
        redis = SimpleNamespace(
            hscan=AsyncMock(side_effect=[(17, rows), (0, {b"999": b"[]"})]),
            ttl=AsyncMock(return_value=60),
            hlen=AsyncMock(return_value=361),
        )
        actions = make_actions(redis)
        first = await actions.execute("ai_cache.tools", {"chat_tid": -10055, "limit": 2})
        assert isinstance(first, dict)
        response = AiCacheResponse.model_validate({**first, "run_id": "run-1"})
        assert row_values(response.entries, "message_id") == list(range(180))
        assert row_values(response.invalid_entries, "field") == [f"invalid-{index}" for index in range(180)]
        assert response.next_cursor == 17
        assert response.has_more is True
        assert response.truncated is False

        last = await actions.execute("ai_cache.tools", {"chat_tid": -10055, "cursor": 17, "limit": 2})
        assert isinstance(last, dict)
        response = AiCacheResponse.model_validate({**last, "run_id": "run-1"})
        assert row_values(response.entries, "message_id") == [999]
        assert response.next_cursor == 0
        assert response.has_more is False

    asyncio.run(scenario())


@pytest.mark.parametrize("valid_count", [0, 100])
def test_message_pages_account_for_every_consumed_valid_and_invalid_row(valid_count: int) -> None:
    async def scenario() -> None:
        rows = [
            (MessageType(user_id=1, message_id=index, text=f"message-{index}").model_dump_json(), float(index))
            for index in range(valid_count)
        ]
        rows.extend((f"invalid-{index}", float(index)) for index in range(valid_count, 200))
        last_row = (MessageType(user_id=1, message_id=999, text="last message").model_dump_json(), 999.0)
        redis = SimpleNamespace(
            zrangebyscore=AsyncMock(side_effect=[[*rows, last_row], [last_row]]),
            ttl=AsyncMock(return_value=60),
            zcount=AsyncMock(return_value=201),
        )
        actions = make_actions(redis)
        first = await actions.execute("ai_cache.messages", {"chat_tid": -10055, "limit": 200})
        assert isinstance(first, dict)
        response = AiCacheResponse.model_validate({**first, "run_id": "run-1"})
        assert row_values(response.entries, "message_id") == list(range(valid_count))
        assert row_values(response.invalid_entries, "index") == list(range(valid_count, 200))
        assert response.has_more is True
        next_offset = len(response.entries) + len(response.invalid_entries)
        assert next_offset == 200

        last = await actions.execute("ai_cache.messages", {"chat_tid": -10055, "offset": next_offset, "limit": 200})
        assert isinstance(last, dict)
        response = AiCacheResponse.model_validate({**last, "run_id": "run-1"})
        assert row_values(response.entries, "message_id") == [999]
        assert response.invalid_entries == []
        assert response.has_more is False

    asyncio.run(scenario())


def test_oversized_scan_response_fails_without_returning_an_advanced_cursor() -> None:
    async def scenario() -> None:
        redis = SimpleNamespace(scan=AsyncMock(return_value=(17, [b"x" * 30_000 for _index in range(40)])))
        actions = make_actions(redis)
        with pytest.raises(WorkerActionError) as error:
            await actions.execute("redis.query", {"op": "scan", "limit": 2})
        assert error.value.code == "result_too_large"

    asyncio.run(scenario())


def make_http_state(actions: WorkerActions) -> CollectorState:
    state = CollectorState(
        session_id="session",
        bearer_token="bearer-secret",
        browser_credential="browser-secret",
        csrf_token="csrf-secret",
        api_origin="http://127.0.0.1:8079",
        ui_origin="http://127.0.0.1:5174",
        sanitized_targets={},
        run_id="run-1",
        state="ready",
        known_secrets=("collector-only-secret",),
    )

    async def dispatch(
        request_id: str,
        run_id: str,
        operation: str,
        _channel_generation: int,
        payload: dict[str, JsonValue],
    ) -> ReplyFrame:
        result = await actions.execute(operation, payload)
        return ReplyFrame(request_id=request_id, run_id=run_id, result=result)

    state.control_dispatch = dispatch
    return state


@pytest.mark.parametrize("operation", ["scan", "hash", "set"])
def test_http_redis_cursor_pages_keep_every_row_and_collector_redaction(operation: str) -> None:
    async def scenario() -> None:
        first_rows: dict[bytes, bytes] | list[bytes]
        last_rows: dict[bytes, bytes] | list[bytes]
        if operation == "hash":
            first_rows = {f"field-{index}".encode(): b"collector-only-secret" for index in range(600)}
            last_rows = {b"last-field": b"collector-only-secret"}
            expected: list[JsonValue] = [[f"field-{index}", "[REDACTED]"] for index in range(600)]
            expected_last: list[JsonValue] = [["last-field", "[REDACTED]"]]
        else:
            first_rows = [f"item-{index}-collector-only-secret".encode() for index in range(1_200)]
            last_rows = [b"last-item-collector-only-secret"]
            expected = [f"item-{index}-[REDACTED]" for index in range(1_200)]
            expected_last = ["last-item-[REDACTED]"]
        scan = AsyncMock(side_effect=[(37, first_rows), (0, last_rows)])
        redis = SimpleNamespace(
            scan=scan,
            hscan=scan,
            sscan=scan,
            type=AsyncMock(return_value=operation.encode()),
            ttl=AsyncMock(return_value=60),
        )
        state = make_http_state(make_actions(redis))
        transport = httpx.ASGITransport(app=create_app(state))
        headers = {"Authorization": "Bearer bearer-secret"}
        async with httpx.AsyncClient(transport=transport, base_url=state.api_origin) as client:
            payload = {"op": operation, "pattern" if operation == "scan" else "key": "debug:key", "limit": 2}
            first = await client.post("/api/v1/redis/query", headers=headers, json=payload)
            assert first.status_code == 200
            page = first.json()
            assert page["data"] == expected
            assert page["next_cursor"] == 37
            assert page["has_more"] is True
            assert page["truncated"] is False
            assert page["run_id"] == "run-1"
            assert "collector-only-secret" not in first.text

            last = await client.post(
                "/api/v1/redis/query", headers=headers, json={**payload, "cursor": page["next_cursor"]}
            )
            assert last.status_code == 200
            page = last.json()
            assert page["data"] == expected_last
            assert page["next_cursor"] == 0
            assert page["has_more"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("valid_count", [0, 100])
def test_http_message_pages_keep_consumed_rows_and_offset_metadata(valid_count: int) -> None:
    async def scenario() -> None:
        rows = [
            (
                MessageType(user_id=1, message_id=index, text="collector-only-secret").model_dump_json(),
                float(index),
            )
            for index in range(valid_count)
        ]
        rows.extend((f"collector-only-secret-invalid-{index}", float(index)) for index in range(valid_count, 200))
        last_row = (MessageType(user_id=1, message_id=999, text="collector-only-secret").model_dump_json(), 999.0)
        redis = SimpleNamespace(
            zrangebyscore=AsyncMock(side_effect=[[*rows, last_row], [last_row]]),
            ttl=AsyncMock(return_value=60),
            zcount=AsyncMock(return_value=201),
        )
        state = make_http_state(make_actions(redis))
        transport = httpx.ASGITransport(app=create_app(state))
        headers = {"Authorization": "Bearer bearer-secret"}
        async with httpx.AsyncClient(transport=transport, base_url=state.api_origin) as client:
            first = await client.get("/api/v1/ai-cache/-10055?kind=messages&limit=200", headers=headers)
            assert first.status_code == 200
            page = first.json()
            assert [entry["message_id"] for entry in page["entries"]] == list(range(valid_count))
            assert [entry["text"] for entry in page["entries"]] == ["[REDACTED]"] * valid_count
            assert [entry["index"] for entry in page["invalid_entries"]] == list(range(valid_count, 200))
            assert page["offset"] == 0
            assert page["has_more"] is True
            assert "collector-only-secret" not in first.text
            next_offset = len(page["entries"]) + len(page["invalid_entries"])
            assert next_offset == 200

            last = await client.get(
                f"/api/v1/ai-cache/-10055?kind=messages&offset={next_offset}&limit=200", headers=headers
            )
            assert last.status_code == 200
            page = last.json()
            assert [entry["message_id"] for entry in page["entries"]] == [999]
            assert page["entries"][0]["text"] == "[REDACTED]"
            assert page["offset"] == 200
            assert page["has_more"] is False

    asyncio.run(scenario())


def test_http_tool_pages_keep_every_valid_and_malformed_row_and_cursor() -> None:
    async def scenario() -> None:
        rows = {str(index).encode(): b"[]" for index in range(180)}
        rows.update({f"invalid-{index}".encode(): b"collector-only-secret" for index in range(180)})
        redis = SimpleNamespace(
            hscan=AsyncMock(side_effect=[(17, rows), (0, {b"999": b"[]"})]),
            ttl=AsyncMock(return_value=60),
            hlen=AsyncMock(return_value=361),
        )
        state = make_http_state(make_actions(redis))
        transport = httpx.ASGITransport(app=create_app(state))
        headers = {"Authorization": "Bearer bearer-secret"}
        async with httpx.AsyncClient(transport=transport, base_url=state.api_origin) as client:
            first = await client.get("/api/v1/ai-cache/-10055?kind=tools&limit=2", headers=headers)
            assert first.status_code == 200
            page = first.json()
            assert [entry["message_id"] for entry in page["entries"]] == list(range(180))
            assert [entry["field"] for entry in page["invalid_entries"]] == [f"invalid-{index}" for index in range(180)]
            assert [entry["value"] for entry in page["invalid_entries"]] == ["[REDACTED]"] * 180
            assert page["next_cursor"] == 17
            assert page["has_more"] is True
            assert "collector-only-secret" not in first.text

            last = await client.get(
                f"/api/v1/ai-cache/-10055?kind=tools&cursor={page['next_cursor']}&limit=2", headers=headers
            )
            assert last.status_code == 200
            page = last.json()
            assert [entry["message_id"] for entry in page["entries"]] == [999]
            assert page["next_cursor"] == 0
            assert page["has_more"] is False

    asyncio.run(scenario())


def test_http_collector_redaction_cannot_expand_a_page_past_the_reply_cap() -> None:
    async def scenario() -> None:
        keys = [f"key-{index}-{'ab' * 100}".encode() for index in range(1_200)]
        redis = SimpleNamespace(scan=AsyncMock(return_value=(37, keys)))
        state = make_http_state(make_actions(redis))
        state.known_secrets = ("ab",)
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url=state.api_origin) as client:
            response = await client.post(
                "/api/v1/redis/query",
                headers={"Authorization": "Bearer bearer-secret"},
                json={"op": "scan", "limit": 2},
            )
            assert response.status_code == 503
            result = response.json()
            assert result["error"]["code"] == "invalid_worker_reply"
            assert "next_cursor" not in result
            assert "data" not in result

    asyncio.run(scenario())
