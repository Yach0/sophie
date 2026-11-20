from aiogram import Bot
from aiogram.filters import Filter
from aiogram.types import Message, MessageEntity

from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_mode import AIMode


class AIBotMentionFilter(Filter):
    async def __call__(  # ty: ignore[incompatible-mutable-override]
        self, message: Message, bot: Bot, ai_mode: AIMode
    ) -> bool | dict[str, str]:
        if ai_mode != AIMode.entertainment or message.chat.type not in {"group", "supergroup"}:
            return False

        text = message.text or message.caption
        entities = message.entities if message.text else message.caption_entities
        mentions = [entity for entity in entities or () if entity.type == "mention"]
        # Reserve every configured command prefix, including captions and commands
        # whose handlers are disabled or reject the caller later in the pipeline.
        if not text or text[0] in CONFIG.commands_prefix or not mentions:
            return False

        username = (await bot.me()).username
        if not username:
            return False
        addressed = [entity for entity in mentions if entity.extract_from(text)[1:].lower() == username.lower()]
        if not addressed:
            return False

        # Telegram offsets count UTF-16 code units; let aiogram extract each retained span.
        parts: list[str] = []
        offset = 0
        for entity in sorted(addressed, key=lambda mention: mention.offset):
            parts.append(MessageEntity(type="mention", offset=offset, length=entity.offset - offset).extract_from(text))
            offset = entity.offset + entity.length
        text_length = len(text.encode("utf-16-le")) // 2
        parts.append(MessageEntity(type="mention", offset=offset, length=text_length - offset).extract_from(text))
        return {"ai_mention_prompt": "".join(parts).strip()}
