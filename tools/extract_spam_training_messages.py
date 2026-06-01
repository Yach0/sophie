from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from sophie_bot.config import CONFIG

DEFAULT_REASON_KEYWORDS = ("spam", "flood", "scam", "phishing")
MONGO_URI_PREFIXES = ("mongodb://", "mongodb+srv://")


def build_reason_regex(reason_keywords: Sequence[str]) -> re.Pattern[str]:
    normalized_keywords = [reason_keyword.strip() for reason_keyword in reason_keywords if reason_keyword.strip()]
    if not normalized_keywords:
        raise ValueError("At least one non-empty reason keyword is required.")

    pattern = "|".join(re.escape(reason_keyword) for reason_keyword in normalized_keywords)
    return re.compile(pattern, re.IGNORECASE)


def build_logs_query(reason_regex: re.Pattern[str]) -> dict[str, object]:
    return {
        "details.original_message_text": {"$exists": True, "$type": "string", "$ne": ""},
        "details.reason": {"$exists": True, "$type": "string", "$regex": reason_regex},
    }


def normalize_message_text(message_text: str) -> str:
    return "\n".join(line.rstrip() for line in message_text.strip().splitlines())


def document_to_row(document: Mapping[str, object]) -> dict[str, object] | None:
    details = document.get("details")
    if not isinstance(details, Mapping):
        return None

    original_message_text = details.get("original_message_text")
    reason = details.get("reason")
    if not isinstance(original_message_text, str) or not isinstance(reason, str):
        return None

    message_text = normalize_message_text(original_message_text)
    if not message_text:
        return None

    timestamp = document.get("timestamp")
    if isinstance(timestamp, datetime):
        timestamp_value = timestamp.isoformat()
    else:
        timestamp_value = None

    return {
        "text": message_text,
        "label": "spam",
        "reason": reason,
        "event": document.get("event"),
        "timestamp": timestamp_value,
        "log_id": str(document.get("_id")),
    }


async def iter_matching_rows(
    collection: AsyncCollection[dict[str, object]],
    reason_keywords: Sequence[str],
    limit: int | None,
) -> AsyncIterator[dict[str, object]]:
    reason_regex = build_reason_regex(reason_keywords)
    query = build_logs_query(reason_regex)
    projection = {
        "_id": 1,
        "event": 1,
        "timestamp": 1,
        "details.reason": 1,
        "details.original_message_text": 1,
    }
    cursor = collection.find(query, projection).sort("timestamp", 1)
    if limit is not None:
        cursor = cursor.limit(limit)

    async for document in cursor:
        row = document_to_row(document)
        if row:
            yield row


def write_jsonl(rows: Sequence[dict[str, object]], output: TextIO) -> None:
    for row in rows:
        output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")


def create_mongo_client(mongo_host: str, mongo_port: int) -> AsyncMongoClient:
    if mongo_host.startswith(MONGO_URI_PREFIXES):
        return AsyncMongoClient(mongo_host)

    return AsyncMongoClient(mongo_host, mongo_port)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract spam-like messages from logs for classifier training.",
    )
    parser.add_argument(
        "--reason",
        dest="reasons",
        action="append",
        default=[],
        help=(
            "Reason keyword to match case-insensitively. "
            "Can be passed multiple times. Defaults to spam, flood, scam, phishing."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSONL output file path. Defaults to stdout.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of exported messages.",
    )
    parser.add_argument(
        "--mongo-db",
        default=CONFIG.mongo_db,
        help="MongoDB database name. Defaults to config mongo_db.",
    )
    parser.add_argument(
        "--mongo-host",
        default=CONFIG.mongo_host,
        help="MongoDB host or URI. Defaults to config mongo_host.",
    )
    parser.add_argument(
        "--mongo-port",
        type=int,
        default=CONFIG.mongo_port,
        help="MongoDB port for plain hostnames. Ignored when --mongo-host is a MongoDB URI.",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be greater than zero.")

    reason_keywords = args.reasons or list(DEFAULT_REASON_KEYWORDS)
    client = create_mongo_client(args.mongo_host, args.mongo_port)
    try:
        collection = client[args.mongo_db]["logs"]
        rows = [row async for row in iter_matching_rows(collection, reason_keywords, args.limit)]

        if args.output:
            with args.output.open("w", encoding="utf-8", newline="") as output_file:
                write_jsonl(rows, output_file)
        else:
            write_jsonl(rows, sys.stdout)

        print(f"Exported {len(rows)} messages.", file=sys.stderr)
        return 0
    finally:
        await client.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
