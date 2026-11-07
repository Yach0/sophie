from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from pydantic_ai.messages import BinaryContent

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.handlers import translate
from sophie_bot.modules.ai.handlers.translate import _resolve_translation_input
from sophie_bot.modules.ai.json_schemas.translate import AITranslateResponseSchema
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


@pytest.mark.parametrize(
    ("mime_type", "has_thumbnail", "caption", "accepted"),
    [
        ("image/gif", True, None, True),
        ("image/gif", False, None, False),
        ("application/pdf", True, None, False),
        ("image/png", True, None, False),
        ("image/gif", False, "Translate me", True),
    ],
)
async def test_translate_document_reply(
    mime_type: str,
    has_thumbnail: bool,
    caption: str | None,
    accepted: bool,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thumbnail = {"file_id": "thumbnail", "file_unique_id": "thumb", "width": 100, "height": 100}
    reply = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Author"},
            "caption": caption,
            "document": {
                "file_id": "original-gif",
                "file_unique_id": "document",
                "file_name": "image.gif",
                "mime_type": mime_type,
                "thumbnail": thumbnail if has_thumbnail else None,
            },
        }
    )
    command = reply.model_copy(
        update={"message_id": 2, "document": None, "caption": None, "text": "/tr", "reply_to_message": reply}
    )
    download = AsyncMock(return_value=BytesIO(b"downloaded thumbnail"))
    monkeypatch.setattr(test_services.bot, "download", download)
    response = AsyncMock()
    monkeypatch.setattr(Message, "reply", response)
    streamer = SimpleNamespace(update_processing_activity=AsyncMock(), send_final=AsyncMock(), response_message=None)
    monkeypatch.setattr(translate, "_start_translation_progress", AsyncMock(return_value=streamer))
    monkeypatch.setattr(translate, "get_value", AsyncMock(return_value="Translate"))
    monkeypatch.setattr(translate, "get_chat_translations_model_plan", AsyncMock())
    monkeypatch.setattr(translate, "resolve_chat_service_tier", AsyncMock())
    monkeypatch.setattr(translate, "get_quota_info", AsyncMock(return_value=None))
    model = AsyncMock(
        return_value=SimpleNamespace(
            output=AITranslateResponseSchema(
                needs_translation=True,
                origin_language_name="English",
                origin_language_emoji="🇬🇧",
                translated_text="Translated",
                translation_explanations=None,
            )
        )
    )
    monkeypatch.setattr(translate, "run_structured_task", model)
    handler = translate.AiTranslate(
        command,
        services=test_services,
        i18n=SimpleNamespace(current_locale_display="German"),
        context=SimpleNamespace(connection=SimpleNamespace(db_model=SimpleNamespace(iid="chat", tid=483808054))),
    )

    await handler.handle()

    if not accepted:
        model.assert_not_awaited()
        download.assert_not_awaited()
        response.assert_awaited_once_with("Please provide text to translate.")
        return

    model.assert_awaited_once()
    context = model.call_args.args[2]
    media = [part for part in context.prompt if isinstance(part, BinaryContent)]
    if has_thumbnail:
        assert media == [BinaryContent(data=b"downloaded thumbnail", media_type="image/jpeg")]
        download.assert_awaited_once_with("thumbnail")
    else:
        assert context.prompt == [caption]
        download.assert_not_awaited()
