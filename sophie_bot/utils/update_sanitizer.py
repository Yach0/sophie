"""Repair Telegram ``MessageOrigin`` payloads that arrive without their ``type`` discriminator.

Around 2026-07-10 Telegram started sending some ``forward_origin`` objects with the ``type`` field
omitted (SOPHIE-284). aiogram models ``MessageOrigin`` as a discriminated union, so pydantic cannot
resolve the variant and rejects the *entire* Update. The update is then lost:

- on webhooks the update is dropped, so replies to forwarded posts are silently ignored;
- on polling it is worse -- ``getUpdates`` never returns successfully, the offset is never advanced,
  and the same batch is retried until Telegram expires it.

The variant is recoverable from the payload shape, because each ``MessageOrigin`` carries a field
unique to it. This module infers the discriminator and injects it before validation.

It is deliberately conservative: anything that does not look exactly like a ``MessageOrigin`` is
left untouched, so aiogram still raises rather than us guessing a wrong variant and mis-parsing.
"""

from __future__ import annotations

import json
from typing import Any, Final

from aiogram.enums import MessageOriginType

from sophie_bot.config import CONFIG
from sophie_bot.utils.logger import log

# Keys whose value is a MessageOrigin: Message.forward_origin and ExternalReplyInfo.origin.
_ORIGIN_KEYS: Final[frozenset[str]] = frozenset({"forward_origin", "origin"})

# Suffix shared by every key above, including the closing quote so it matches a JSON key rather than
# the word "origin" in message text. Checking the raw body first keeps the walk off the hot path for
# the payloads that cannot contain an origin.
_ORIGIN_MARKER: Final[str] = 'origin"'

# Fields unique to a single MessageOrigin variant, in resolution order. MessageOriginChannel is
# last because it is the only one identified by a pair rather than a single field.
_UNIQUE_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("sender_user", MessageOriginType.USER.value),
    ("sender_user_name", MessageOriginType.HIDDEN_USER.value),
    ("sender_chat", MessageOriginType.CHAT.value),
)


def _infer_origin_type(origin: dict[str, Any]) -> str | None:
    """Infer the MessageOrigin discriminator from the fields present, or None if ambiguous."""
    for field, origin_type in _UNIQUE_FIELDS:
        if field in origin:
            return origin_type
    if "chat" in origin and "message_id" in origin:
        return MessageOriginType.CHANNEL.value
    return None


def _repair_origin(origin: dict[str, Any]) -> str | None:
    """Inject the missing discriminator into a single origin. Returns the type, or None if untouched."""
    # `date` is required on every variant, so its absence means this is not a MessageOrigin
    # (ExternalReplyInfo.origin aside, `origin` is also a plain string on UniqueGiftInfo).
    if "type" in origin or "date" not in origin:
        return None

    origin_type = _infer_origin_type(origin)
    if origin_type is None:
        return None

    origin["type"] = origin_type
    return origin_type


def sanitize_message_origins(payload: Any) -> list[str]:
    """Recursively repair MessageOrigin payloads in-place.

    Returns the list of injected discriminators, so callers can log that the workaround fired and
    tell when Telegram no longer needs it. An empty list means nothing was changed.
    """
    repaired: list[str] = []
    _walk(payload, repaired)
    return repaired


def _walk(node: Any, repaired: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _ORIGIN_KEYS and isinstance(value, dict):
                origin_type = _repair_origin(value)
                if origin_type is not None:
                    repaired.append(origin_type)
            _walk(value, repaired)
    elif isinstance(node, list):
        for item in node:
            _walk(item, repaired)


def sanitizing_json_loads(value: str | bytes) -> Any:
    """JSON loader that repairs MessageOrigin payloads missing their `type` discriminator.

    aiogram routes both ingress paths through `session.json_loads` -- polling via `check_response`
    and webhooks via `request.json(loads=...)` -- so hooking here covers both, ahead of the pydantic
    validation that would otherwise reject the whole Update.

    Pass this to `AiohttpSession(json_loads=...)` wherever a Bot is constructed.
    """
    data = json.loads(value)

    if not CONFIG.updates_sanitize_message_origin:
        return data

    has_marker = _ORIGIN_MARKER in value if isinstance(value, str) else _ORIGIN_MARKER.encode() in value
    if not has_marker:
        return data

    repaired = sanitize_message_origins(data)
    if repaired:
        # Summarise: a batch can carry hundreds of origins, and logging each one floods the log.
        log.warning(
            "Repaired MessageOrigin payloads missing 'type'",
            repaired=len(repaired),
            types=sorted(set(repaired)),
        )

    return data
