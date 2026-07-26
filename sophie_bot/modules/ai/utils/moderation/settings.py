from __future__ import annotations

from typing import Final

from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory

# The order a category walks through when its button is pressed.
LEVEL_CYCLE: Final[tuple[DetectionLevel, ...]] = (
    DetectionLevel.OFF,
    DetectionLevel.LOW,
    DetectionLevel.NORMAL,
    DetectionLevel.HIGH,
)


def next_level(level: DetectionLevel) -> DetectionLevel:
    return LEVEL_CYCLE[(LEVEL_CYCLE.index(level) + 1) % len(LEVEL_CYCLE)]


async def get_moderator_settings(chat_iid: PydanticObjectId) -> AIModeratorModel | None:
    return await AIModeratorModel.find_one(AIModeratorModel.chat.id == chat_iid)


def get_levels(settings: AIModeratorModel | None) -> dict[ModerationCategory, DetectionLevel]:
    """Every category's level, defaulting to NORMAL for a chat that never configured them."""
    if settings is None:
        return dict.fromkeys(ModerationCategory, DetectionLevel.NORMAL)
    return {category: getattr(settings, category.value, DetectionLevel.NORMAL) for category in ModerationCategory}


async def set_category_level(
    chat: ChatModel,
    category: ModerationCategory,
    level: DetectionLevel,
) -> AIModeratorModel:
    settings = await get_moderator_settings(chat.iid) or AIModeratorModel(chat=chat)
    setattr(settings, category.value, level)
    await settings.save()
    return settings
