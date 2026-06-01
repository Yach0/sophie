from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

from tools.extract_spam_training_messages import (
    build_logs_query,
    build_reason_regex,
    create_mongo_client,
    document_to_row,
    write_jsonl,
)


def test_build_logs_query_matches_reason_and_original_message_text() -> None:
    reason_regex = build_reason_regex(["spam", "flood"])

    query = build_logs_query(reason_regex)

    assert query["details.original_message_text"] == {"$exists": True, "$type": "string", "$ne": ""}
    assert query["details.reason"] == {"$exists": True, "$type": "string", "$regex": reason_regex}
    assert reason_regex.search("Likely SPAM message")
    assert reason_regex.search("flood detected")


def test_document_to_row_extracts_training_fields() -> None:
    document = {
        "_id": "log-id",
        "event": "user_banned",
        "timestamp": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "details": {
            "reason": "Spam links",
            "original_message_text": "  buy now  \nlimited offer  ",
        },
    }

    row = document_to_row(document)

    assert row == {
        "text": "buy now\nlimited offer",
        "label": "spam",
        "reason": "Spam links",
        "event": "user_banned",
        "timestamp": "2026-06-01T00:00:00+00:00",
        "log_id": "log-id",
    }


def test_document_to_row_skips_documents_without_text() -> None:
    document = {
        "_id": "log-id",
        "details": {
            "reason": "Spam links",
            "original_message_text": "   ",
        },
    }

    assert document_to_row(document) is None


def test_write_jsonl() -> None:
    rows = [
        {
            "text": "buy now",
            "label": "spam",
            "reason": "Spam links",
            "event": "user_banned",
            "timestamp": "2026-06-01T00:00:00+00:00",
            "log_id": "log-id",
        }
    ]

    jsonl_output = StringIO()
    write_jsonl(rows, jsonl_output)
    assert jsonl_output.getvalue() == (
        '{"text":"buy now","label":"spam","reason":"Spam links","event":"user_banned",'
        '"timestamp":"2026-06-01T00:00:00+00:00","log_id":"log-id"}\n'
    )


def test_create_mongo_client_uses_uri_without_separate_port() -> None:
    with patch("tools.extract_spam_training_messages.AsyncMongoClient") as client_cls:
        create_mongo_client("mongodb://user:password@example.com:27017/sophie", 12345)

    client_cls.assert_called_once_with("mongodb://user:password@example.com:27017/sophie")


def test_create_mongo_client_uses_port_for_plain_hostname() -> None:
    with patch("tools.extract_spam_training_messages.AsyncMongoClient") as client_cls:
        create_mongo_client("localhost", 12345)

    client_cls.assert_called_once_with("localhost", 12345)
