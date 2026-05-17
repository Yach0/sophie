from __future__ import annotations

from typing import Any

from aiogram.types import Message


def extract_offending_message_text(message: Message | None) -> str | None:
    """Return the text/caption from the message that caused a restriction."""
    if not message:
        return None

    return message.text or message.caption or None


def add_offending_message_text(details: dict[str, Any], message: Message | None) -> dict[str, Any]:
    """Add original offending message text to restriction log details when available."""
    offending_message_text = extract_offending_message_text(message)
    if offending_message_text:
        details["original_message_text"] = offending_message_text

    return details
