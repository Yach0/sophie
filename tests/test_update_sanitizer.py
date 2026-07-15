import json
from typing import Any

import pytest
from aiogram.types import Update
from pydantic import ValidationError

from sophie_bot.utils.update_sanitizer import sanitize_message_origins, sanitizing_json_loads


def _chat(chat_id: int, chat_type: str = "supergroup") -> dict[str, Any]:
    return {"id": chat_id, "type": chat_type, "title": "Chat"}


def _origin_channel() -> dict[str, Any]:
    return {"date": 1784119264, "chat": _chat(-100200, "channel"), "message_id": 10974, "author_signature": "Beam 4"}


def _origin_user() -> dict[str, Any]:
    return {"date": 1784119264, "sender_user": {"id": 5, "is_bot": False, "first_name": "User"}}


def _origin_hidden_user() -> dict[str, Any]:
    return {"date": 1784119264, "sender_user_name": "Hidden"}


def _origin_chat() -> dict[str, Any]:
    return {"date": 1784119264, "sender_chat": _chat(-100300), "author_signature": "Admin"}


def _update_with_origin(origin: dict[str, Any]) -> dict[str, Any]:
    """An update replying to a forwarded message, mirroring the SOPHIE-284 payload."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "date": 1784119264,
            "chat": _chat(-1001),
            "from": {"id": 5, "is_bot": False, "first_name": "User"},
            "text": "reply",
            "reply_to_message": {
                "message_id": 10974,
                "date": 1784119264,
                "chat": _chat(-1001),
                "forward_origin": origin,
                "text": "forwarded post",
            },
        },
    }


@pytest.mark.parametrize(
    ("origin_factory", "expected_type"),
    [
        (_origin_channel, "channel"),
        (_origin_user, "user"),
        (_origin_hidden_user, "hidden_user"),
        (_origin_chat, "chat"),
    ],
)
def test_infers_missing_discriminator_for_every_variant(origin_factory: Any, expected_type: str) -> None:
    payload = _update_with_origin(origin_factory())

    assert sanitize_message_origins(payload) == [expected_type]
    assert payload["message"]["reply_to_message"]["forward_origin"]["type"] == expected_type


def test_repaired_update_validates_as_the_right_variant() -> None:
    """The regression: without the injected discriminator aiogram rejects the whole Update."""
    payload = _update_with_origin(_origin_channel())

    with pytest.raises(ValidationError, match="forward_origin"):
        Update.model_validate(payload, context={"bot": None})

    sanitize_message_origins(payload)
    update = Update.model_validate(payload, context={"bot": None})

    origin = update.message.reply_to_message.forward_origin  # type: ignore[union-attr]
    assert origin.type == "channel"
    assert origin.author_signature == "Beam 4"


def test_well_formed_payload_is_untouched() -> None:
    origin = _origin_channel() | {"type": "channel"}
    payload = _update_with_origin(origin)
    before = json.dumps(payload, sort_keys=True)

    assert sanitize_message_origins(payload) == []
    assert json.dumps(payload, sort_keys=True) == before


def test_edited_message_origin_is_repaired() -> None:
    payload = {
        "update_id": 1,
        "edited_message": {
            "message_id": 2,
            "date": 1784119264,
            "chat": _chat(-1001),
            "forward_origin": _origin_channel(),
        },
    }

    assert sanitize_message_origins(payload) == ["channel"]


def test_external_reply_origin_is_repaired() -> None:
    payload = {"message": {"external_reply": {"origin": _origin_channel(), "message_id": 3}}}

    assert sanitize_message_origins(payload) == ["channel"]


@pytest.mark.parametrize(
    "origin",
    [
        pytest.param({"date": 1784119264, "mystery": 1}, id="unknown-shape"),
        pytest.param({"date": 1784119264, "chat": {"id": -1}}, id="channel-without-message-id"),
        pytest.param({"chat": {"id": -1}, "message_id": 1}, id="no-date-so-not-an-origin"),
    ],
)
def test_fails_open_on_unrecognized_shapes(origin: dict[str, Any]) -> None:
    """Never guess: leave the payload for aiogram to reject rather than mis-parse it."""
    payload = {"message": {"forward_origin": origin}}

    assert sanitize_message_origins(payload) == []
    assert "type" not in payload["message"]["forward_origin"]


def test_string_origin_is_not_touched() -> None:
    """UniqueGiftInfo.origin is a plain string, not a MessageOrigin."""
    payload = {"unique_gift": {"origin": "upgrade"}}

    assert sanitize_message_origins(payload) == []


def test_json_loads_hook_repairs_and_respects_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"ok": True, "result": [_update_with_origin(_origin_channel())]})

    repaired = sanitizing_json_loads(body)
    assert repaired["result"][0]["message"]["reply_to_message"]["forward_origin"]["type"] == "channel"

    monkeypatch.setattr("sophie_bot.utils.update_sanitizer.CONFIG.updates_sanitize_message_origin", False)
    untouched = sanitizing_json_loads(body)
    assert "type" not in untouched["result"][0]["message"]["reply_to_message"]["forward_origin"]


def test_json_loads_hook_accepts_bytes() -> None:
    body = json.dumps({"result": [_update_with_origin(_origin_channel())]}).encode()

    repaired = sanitizing_json_loads(body)
    assert repaired["result"][0]["message"]["reply_to_message"]["forward_origin"]["type"] == "channel"
