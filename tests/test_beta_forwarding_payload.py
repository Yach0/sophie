from __future__ import annotations

import json
from functools import partial
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineQuery, PollAnswer, Update, User

from sophie_bot.db.models import BetaModeModel, ChatModel
from sophie_bot.db.models.beta import CurrentMode
from sophie_bot.middlewares.beta import BetaMiddleware
from sophie_bot.middlewares.save_chats import SaveChatsMiddleware

RICH_MESSAGE_UPDATE: dict = {
    "update_id": 1,
    "callback_query": {
        "id": "2077939772276452595",
        "chat_instance": "2043431885968881451",
        "data": "pmhelpmod:ai:1",
        "from": {"id": 483808054, "is_bot": False, "first_name": "yachu"},
        "message": {
            "message_id": 2467647,
            "date": 1784942750,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": {
                "blocks": [
                    {"type": "heading", "text": "Help", "size": 1},
                    {"type": "paragraph", "text": "There are three ways to find your way around Sophie:"},
                    {
                        "type": "list",
                        "items": [{"label": "•", "blocks": [{"type": "paragraph", "text": "Ask in your words"}]}],
                    },
                ]
            },
        },
    },
}


def test_forwarded_payload_stays_parsable_by_the_receiving_instance() -> None:
    update = Update.model_validate(RICH_MESSAGE_UPDATE)

    payload = json.loads(BetaMiddleware().get_data(update))

    # Discriminator tags are field defaults; dropping them made the payload unparsable (SOPHIE-284).
    assert [block["type"] for block in payload["callback_query"]["message"]["rich_message"]["blocks"]] == [
        "heading",
        "paragraph",
        "list",
    ]
    assert Update.model_validate(payload) == update


def test_forwarded_payload_drops_bot_default_sentinels() -> None:
    update = Update.model_validate(
        {
            "update_id": 2,
            "message": {
                "message_id": 1,
                "date": 1784942750,
                "chat": {"id": 483808054, "type": "private"},
                "text": "https://example.com",
                "link_preview_options": {"url": "https://example.com"},
            },
        }
    )

    payload = json.loads(BetaMiddleware().get_data(update))

    # Unset LinkPreviewOptions fields hold Default sentinels pydantic cannot serialize.
    assert payload["message"]["link_preview_options"] == {
        "is_disabled": None,
        "url": "https://example.com",
        "prefer_small_media": None,
        "prefer_large_media": None,
        "show_above_text": None,
    }
    forwarded = Update.model_validate(payload)
    assert forwarded.message is not None
    assert forwarded.message.text == "https://example.com"


def test_forwarded_payload_keeps_dates_as_unix_timestamps() -> None:
    update = Update.model_validate(RICH_MESSAGE_UPDATE)

    payload = json.loads(BetaMiddleware().get_data(update))

    assert payload["callback_query"]["message"]["date"] == 1784942750


@pytest.mark.parametrize("event_type", ("inline_query", "poll_answer"))
async def test_chatless_updates_follow_the_actors_beta_assignment(
    event_type: Literal["inline_query", "poll_answer"],
    db_init: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(id=7_650_321_001, is_bot=False, first_name="Beta user")
    actor = await ChatModel.upsert_user(user)
    try:
        await BetaModeModel.set_mode(actor.iid, CurrentMode.beta)
        if event_type == "inline_query":
            update = Update(
                update_id=1,
                inline_query=InlineQuery(id="query", from_user=user, query="notes", offset=""),
            )
        else:
            update = Update(
                update_id=2,
                poll_answer=PollAnswer(
                    poll_id="poll",
                    user=user,
                    option_ids=[0],
                    option_persistent_ids=["first"],
                ),
            )
        beta = BetaMiddleware()
        forward = AsyncMock()
        stable = AsyncMock()
        monkeypatch.setattr(beta, "send_request", forward)

        await SaveChatsMiddleware()(
            partial(beta, stable),
            update,
            {"event_from_user": user, "event_chat": None},
        )

        forward.assert_awaited_once()
        stable.assert_not_awaited()
    finally:
        await BetaModeModel.find(BetaModeModel.chat.id == actor.iid).delete()
        await actor.delete()
