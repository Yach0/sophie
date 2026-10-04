from __future__ import annotations

from re import Pattern

from bson import Regex


def validate_exact_mongo_id(value: object) -> None:
    """Validate a decoded BSON ID before it is used as a write selector."""
    if value is None or isinstance(value, (list, Regex, Pattern)) or _contains_operator(value):
        raise ValueError("Mongo writes require an exact _id")


def _contains_operator(value: object) -> bool:
    if isinstance(value, (Regex, Pattern)):
        return True
    if isinstance(value, dict):
        return any(str(key).startswith("$") or _contains_operator(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_operator(item) for item in value)
    return False
