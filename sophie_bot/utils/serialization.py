"""Shared pydantic serialization helpers for aiogram objects."""

from __future__ import annotations

from typing import Any

from aiogram.client.default import Default


def serialize_bot_default(value: Any) -> None:
    """``model_dump_json(fallback=...)`` hook that serializes aiogram ``Default`` sentinels as null.

    aiogram fills unset fields of incoming objects (LinkPreviewOptions, ReplyParameters, ...)
    with ``Default`` sentinels, which pydantic cannot serialize. They only mean "the bot decides",
    so the reader is better off seeing nothing at all.

    Unlike ``exclude``, this runs for every value pydantic walks, so sentinels nested inside
    child models are covered too. Anything else still raises, keeping unknown types loud.
    """
    if not isinstance(value, Default):
        raise TypeError(f"Unable to serialize unknown type: {type(value)}")
