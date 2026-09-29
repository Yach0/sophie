from __future__ import annotations

from typing import Any

from aiogram.types import RichMessage, RichMessageButton
from pydantic import BaseModel


def _visible_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(_visible_text(item) for item in value)
    if isinstance(value, str):
        return value
    if isinstance(value, RichMessageButton):
        return _visible_text(value.text)
    if not isinstance(value, BaseModel):
        return str(value)
    name = value.__class__.__name__
    if name.startswith("RichText"):
        if name == "RichTextCustomEmoji":
            return str(value.alternative_text or "")
        if name == "RichTextButton":
            return _visible_text(value.button)
        if name == "RichTextAnchor":
            return ""
        if hasattr(value, "text"):
            return _visible_text(value.text)
        if hasattr(value, "expression"):
            return str(value.expression)
    if name == "RichBlockListItem":
        return f"{value.label} {_visible_text(value.blocks)}"
    if name == "RichBlockTableCell":
        return _visible_text(value.text)
    if name == "RichBlockCaption":
        return _visible_text(value.text) + (f" ({_visible_text(value.credit)})" if value.credit else "")
    if name == "RichBlockButtons":
        return " ".join(_visible_text(button) for button in value.buttons)
    if name in {
        "RichBlockAnimation",
        "RichBlockAudio",
        "RichBlockDocument",
        "RichBlockMap",
        "RichBlockPhoto",
        "RichBlockVideo",
        "RichBlockVoiceNote",
    }:
        media = next(
            (
                getattr(value, field, None)
                for field in ("animation", "audio", "document", "photo", "video", "voice_note")
                if getattr(value, field, None)
            ),
            None,
        )
        if isinstance(media, list) and media:
            media = max(
                media,
                key=lambda candidate: (
                    int(getattr(candidate, "width", 0) or 0) * int(getattr(candidate, "height", 0) or 0),
                    int(getattr(candidate, "file_size", 0) or 0),
                ),
            )
        media_label = getattr(media, "file_name", None) or name.removeprefix("RichBlock")
        return f"[{media_label}]" + (f" {_visible_text(value.caption)}" if value.caption else "")
    if hasattr(value, "blocks"):
        return _visible_text(value.blocks)
    if hasattr(value, "items"):
        return _visible_text(value.items)
    if hasattr(value, "cells"):
        return _visible_text(value.cells)
    if hasattr(value, "text"):
        return _visible_text(value.text)
    if hasattr(value, "caption"):
        return _visible_text(value.caption)
    return ""


def rich_message_to_plain_text(message: RichMessage) -> str:
    """Project a RichMessage to visible text without Telegram markup."""
    return _visible_text(message)
