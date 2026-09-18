from __future__ import annotations

import asyncio
import os
import socket
import uuid
from typing import cast
from urllib.parse import urlsplit

import pytest
from pymongo.uri_parser import parse_uri
from redis.asyncio import Redis
from redis.exceptions import ResponseError

import sophie_bot.services.db as database_service
from debug.adapters import EventRecorder
from debug.adapters.mongo import install_mongo_observer
from debug.adapters.redis import install_redis_observer
from debug.capture import TelemetrySink
from debug.protocol import (
    CONTROL_FRAME_LIMIT,
    CapturedEvent,
    Category,
    Phase,
    StatusFrame,
    encode_frame,
    strict_json_loads,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[CapturedEvent] = []
        self.recorder_errors = 0

    def emit(self, event: CapturedEvent) -> None:
        self.events.append(event)


def integration_target(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(f"{name} is required for disposable service integration")
    hostname = urlsplit(value).hostname
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail(f"{name} must use a loopback host")
    return value


def test_real_service_adapters_preserve_driver_behavior() -> None:
    async def scenario() -> None:
        mongo_uri = integration_target("DEBUG_TEST_MONGO_URI")
        redis_url = integration_target("DEBUG_TEST_REDIS_URL")
        sink = RecordingSink()
        recorder = EventRecorder(cast(TelemetrySink, sink))
        mongo_listener, restore_mongo = install_mongo_observer(recorder)
        restore_redis = install_redis_observer(recorder)
        mongo_client = database_service.AsyncMongoClient(mongo_uri, serverSelectionTimeoutMS=2_000)
        redis = Redis.from_url(redis_url)
        document_id = uuid.uuid4().hex
        redis_key = f"messages:{-int(uuid.uuid4().int % 1_000_000_000) - 1}"
        database_name = parse_uri(mongo_uri)["database"]
        assert database_name is not None
        collection = mongo_client[database_name]["debug_gui_integration"]
        try:
            await mongo_client.admin.command("ping")
            insert = await collection.insert_one({"_id": document_id, "value": 1})
            assert insert.inserted_id == document_id
            assert await collection.find_one({"_id": document_id}) == {"_id": document_id, "value": 1}

            assert await redis.ping() is True
            pipeline = redis.pipeline(transaction=False)
            pipeline.set(redis_key, b"value", ex=30)
            pipeline.get(redis_key)
            assert await pipeline.execute() == [True, b"value"]

            errors = redis.pipeline(transaction=False)
            errors.set(redis_key, b"not-an-integer", ex=30)
            errors.incr(redis_key)
            error_results = await errors.execute(raise_on_error=False)
            assert error_results[0] is True
            assert isinstance(error_results[1], ResponseError)

            watching = redis.pipeline()
            await watching.watch(redis_key)
            assert await watching.get(redis_key) == b"not-an-integer"
            await watching.reset()
        finally:
            try:
                await collection.delete_one({"_id": document_id})
            finally:
                try:
                    await redis.delete(redis_key)
                finally:
                    restore_redis()
                    restore_mongo()
                    mongo_listener.clear()
                    await redis.aclose()
                    await mongo_client.close()

        mongo_events = [event for event in sink.events if event.category is Category.MONGO]
        redis_events = [event for event in sink.events if event.category is Category.REDIS]
        ai_events = [event for event in sink.events if event.category is Category.AI_CACHE]
        assert any(event.name == "insert" for event in mongo_events)
        assert any(event.name == "find" for event in mongo_events)
        pipeline_finish = next(
            event for event in redis_events if event.name == "pipeline" and event.phase is Phase.FINISH
        )
        assert isinstance(pipeline_finish.payload, dict)
        assert pipeline_finish.payload["aggregate_timing"] is True
        pipeline_results = pipeline_finish.payload["results"]
        assert isinstance(pipeline_results, list)
        assert all(isinstance(result, dict) and "duration_ms" not in result for result in pipeline_results)
        assert any(event.parent_span_id == pipeline_finish.span_id for event in ai_events)
        assert sink.recorder_errors == 0

    asyncio.run(scenario())


def test_telemetry_overload_does_not_break_real_redis() -> None:
    async def scenario() -> None:
        redis_url = integration_target("DEBUG_TEST_REDIS_URL")
        sender, receiver = socket.socketpair()
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1_024)
        sink = TelemetrySink(sender, "overload-run", os.getpid())
        recorder = EventRecorder(sink)
        restore_redis = install_redis_observer(recorder)
        redis = Redis.from_url(redis_url)
        sink.start()
        try:
            for _request_number in range(4_000):
                assert await redis.ping() is True
                if sink.dropped_total > 0:
                    break
            assert sink.dropped_total > 0
            assert await redis.ping() is True

            encoded_status = encode_frame(
                StatusFrame(
                    run_id="overload-run",
                    pid=os.getpid(),
                    state="ready",
                    dropped_total=sink.dropped_total,
                    recorder_errors=sink.recorder_errors,
                ),
                CONTROL_FRAME_LIMIT,
            )
            status = StatusFrame.model_validate(strict_json_loads(encoded_status))
            assert status.dropped_total == sink.dropped_total
        finally:
            restore_redis()
            await redis.aclose()

        receiver.setblocking(False)

        async def drain() -> int:
            received = 0
            while data := await asyncio.get_running_loop().sock_recv(receiver, 64 * 1024):
                received += len(data)
            return received

        drain_task = asyncio.create_task(drain())
        await asyncio.to_thread(sink.close)
        assert await drain_task > 0
        receiver.close()

    asyncio.run(scenario())
