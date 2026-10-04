from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from pydantic import JsonValue

from debug.actions import WorkerActionError, WorkerActions
from debug.capture import REDACTED, TelemetrySink, normalize_payload
from debug.collector import MongoDeleteAction, MongoQueryResponse, MongoUpdateAction, _validate_action
from sophie_bot.runtime import BotModeRuntime


@pytest.mark.parametrize(
    "document_id",
    [
        {"$ne": None},
        {"$gt": ""},
        {"$in": [1]},
        {"$regex": ".*"},
        {"$regularExpression": {"pattern": ".*", "options": ""}},
        {"nested": {"$regex": ".*"}},
        {"nested": {"$ne": 1}},
        [1, 2],
    ],
)
@pytest.mark.parametrize("operation", ["mongo.update_one", "mongo.delete_one"])
@pytest.mark.parametrize("boundary", ["collector", "worker"])
def test_mongo_predicate_ids_rejected_in_collector_and_worker(
    document_id: JsonValue, operation: str, boundary: str
) -> None:
    payload: dict[str, JsonValue] = {"kind": operation, "collection": "records", "_id": document_id}
    if operation == "mongo.update_one":
        payload["update"] = {"$set": {"name": "changed"}}
        action = MongoUpdateAction.model_validate(payload)
    else:
        action = MongoDeleteAction.model_validate(payload)
    if boundary == "collector":
        with pytest.raises(ValueError, match="exact _id"):
            _validate_action(action)
        return

    async def scenario() -> None:
        collection = SimpleNamespace(update_one=AsyncMock(), delete_one=AsyncMock())
        runtime = SimpleNamespace(
            config=SimpleNamespace(model_dump=lambda *, mode: {}),
            services=SimpleNamespace(db=SimpleNamespace(database={"records": collection})),
        )
        actions = WorkerActions(cast(BotModeRuntime, runtime), cast(TelemetrySink, SimpleNamespace()))
        with pytest.raises(WorkerActionError, match="exact _id"):
            await actions.execute(operation, payload)
        collection.update_one.assert_not_awaited()
        collection.delete_one.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("binary_type", [bytes, bytearray, memoryview])
@pytest.mark.parametrize("prefix", [b"text:", b"\xff\x00"])
def test_binary_capture_redacts_credentials_before_encoding(binary_type: type, prefix: bytes) -> None:
    source = prefix + b"known-credential" + b":suffix"
    normalized, truncated, redacted = normalize_payload(binary_type(source), ("known-credential",))
    assert redacted is True
    assert truncated is False
    assert base64.b64decode(normalized["base64"]) == prefix + REDACTED.encode() + b":suffix"


@pytest.mark.parametrize(
    "document_id", ["literal", 42, {"$oid": "507f1f77bcf86cd799439011"}, {"tenant": "one", "number": 2}]
)
def test_collector_accepts_literal_mongo_ids(document_id: JsonValue) -> None:
    _validate_action(
        MongoDeleteAction.model_validate({"kind": "mongo.delete_one", "collection": "records", "_id": document_id})
    )


def test_mongo_page_keeps_metadata_and_every_row_under_entry_budget() -> None:
    async def scenario() -> None:
        documents = [{"_id": ObjectId(), **{f"field-{index}": index for index in range(1_100)}} for _index in range(3)]
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=documents), close=AsyncMock())
        cursor.skip = lambda _value: cursor
        cursor.limit = lambda _value: cursor
        cursor.max_time_ms = lambda _value: cursor
        runtime = SimpleNamespace(
            config=SimpleNamespace(model_dump=lambda *, mode: {}),
            services=SimpleNamespace(
                db=SimpleNamespace(database={"records": SimpleNamespace(find=lambda *_args: cursor)})
            ),
        )
        actions = WorkerActions(cast(BotModeRuntime, runtime), cast(TelemetrySink, SimpleNamespace()))
        result = await actions.execute("mongo.query", {"collection": "records", "limit": 3})
        assert isinstance(result, dict)
        response = MongoQueryResponse.model_validate({**result, "run_id": "run-1"})
        assert len(response.items) == 3
        assert response.has_more is False
        assert response.truncated is True
        assert response.duration_ms >= 0
        assert [row["_id"] for row in response.items if isinstance(row, dict)] == [
            {"$oid": str(document["_id"])} for document in documents
        ]
        cursor.close.assert_awaited_once()

    asyncio.run(scenario())
