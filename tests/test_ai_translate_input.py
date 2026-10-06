import pytest
from aiogram.types import Message

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.handlers.translate import _resolve_translation_input
from sophie_bot.modules.ai.utils.ai_header import AI_BATTERY_CUSTOM_EMOJI_IDS, AI_CUSTOM_EMOJI_ID
from sophie_bot.services.application import ApplicationServices


@pytest.mark.parametrize("author", ["user", "other_bot", "sophie", "anonymous"])
async def test_only_sophie_authored_rich_replies_lose_ai_decorations(
    author: str, test_services: ApplicationServices
) -> None:
    reply = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": [
                            {"type": "custom_emoji", "custom_emoji_id": AI_CUSTOM_EMOJI_ID, "alternative_text": "✨"},
                            " Hello",
                        ],
                    },
                    {
                        "type": "details",
                        "summary": "Summary",
                        "blocks": [{"type": "paragraph", "text": "Body"}],
                    },
                    {
                        "type": "paragraph",
                        "text": [
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": min(AI_BATTERY_CUSTOM_EMOJI_IDS),
                                "alternative_text": "🔋",
                            },
                            " 80%",
                        ],
                    },
                ]
            },
            "from": (
                None
                if author == "anonymous"
                else {
                    "id": CONFIG.bot_id if author == "sophie" else CONFIG.bot_id + 1,
                    "is_bot": author != "user",
                    "first_name": "Author",
                }
            ),
        }
    )
    command = Message.model_validate(
        {
            "message_id": 2,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "text": "/tr",
            "reply_to_message": reply,
        }
    )

    text, is_voice = await _resolve_translation_input(command, {}, services=test_services)

    assert text == ("Hello\nSummary\nBody" if author == "sophie" else "✨ Hello\nSummary\nBody\n🔋 80%")
    assert is_voice is False
