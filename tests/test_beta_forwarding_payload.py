import json

from aiogram.types import Update

from sophie_bot.middlewares.beta import BetaMiddleware

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


def test_forwarded_payload_keeps_dates_as_unix_timestamps() -> None:
    update = Update.model_validate(RICH_MESSAGE_UPDATE)

    payload = json.loads(BetaMiddleware().get_data(update))

    assert payload["callback_query"]["message"]["date"] == 1784942750
