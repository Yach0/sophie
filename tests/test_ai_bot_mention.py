from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, MessageEntity, User

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.filters.bot_mention import AIBotMentionFilter


@pytest.mark.asyncio
@pytest.mark.parametrize("caption", (False, True))
@pytest.mark.parametrize(
    ("text", "entities", "expected"),
    (
        ("@rSophieBot help", [("mention", 0, 11)], "help"),
        ("@RsOpHiEbOt help", [("mention", 0, 11)], "help"),
        ("/add_filter @rSophieBot", [("mention", 12, 11)], None),
        ("!add_filter @rSophieBot", [("mention", 12, 11)], None),
        ("/ai @rSophieBot help", [("mention", 4, 11)], None),
        ("@OtherBot help", [("mention", 0, 9)], None),
        ("a@rSophieBot.org help", [("email", 0, 16)], None),
        ("prefix@rSophieBot help", [], None),
        ("😀 @rSophieBot help @OtherBot", [("mention", 3, 11), ("mention", 20, 9)], "😀  help @OtherBot"),
        ("@rSophieBot help @rSophieBot", [("mention", 0, 11), ("mention", 17, 11)], "help"),
    ),
)
async def test_bot_mention_uses_entities_and_cached_identity(
    monkeypatch: pytest.MonkeyPatch,
    caption: bool,
    text: str,
    entities: list[tuple[str, int, int]],
    expected: str | None,
) -> None:
    bot = Bot("123456:TEST")
    get_me = AsyncMock(return_value=User(id=123456, is_bot=True, first_name="Bot", username="rSophieBot"))
    monkeypatch.setattr(bot, "get_me", get_me)
    parsed_entities = [MessageEntity(type=kind, offset=offset, length=length) for kind, offset, length in entities]
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="supergroup"),
        text=None if caption else text,
        entities=None if caption else parsed_entities,
        caption=text if caption else None,
        caption_entities=parsed_entities if caption else None,
    )
    for _attempt in range(2):
        result = await AIBotMentionFilter()(message, bot, AIMode.entertainment)
        assert result == ({"ai_mention_prompt": expected} if expected is not None else False)
    assert get_me.await_count == (
        1 if not text.startswith(("/", "!")) and any(kind == "mention" for kind, _, _ in entities) else 0
    )
    for mode in (AIMode.support, AIMode.disabled, AIMode.moderation):
        assert await AIBotMentionFilter()(message, bot, mode) is False
    private_message = message.model_copy(update={"chat": Chat(id=123, type="private")})
    assert await AIBotMentionFilter()(private_message, bot, AIMode.entertainment) is False
    await bot.session.close()
