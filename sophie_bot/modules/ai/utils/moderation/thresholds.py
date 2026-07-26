from __future__ import annotations

from typing import Final

from sophie_bot.db.models.ai.ai_moderator import AIModeratorModel, DetectionLevel
from sophie_bot.modules.ai.utils.moderation.providers.base import ModerationProvider
from sophie_bot.utils.feature_flags import FeatureType, get_value

_MIN_THRESHOLD: Final[float] = 0.01
_MAX_THRESHOLD: Final[float] = 1.0

# DetectionLevel.OFF has no multiplier: the category is skipped entirely rather than scaled to zero.
_LEVEL_MULTIPLIER_FLAGS: Final[dict[DetectionLevel, tuple[FeatureType, float]]] = {
    DetectionLevel.LOW: ("ai_moderation_level_low_multiplier", 0.7),
    DetectionLevel.NORMAL: ("ai_moderation_level_normal_multiplier", 1.0),
    DetectionLevel.HIGH: ("ai_moderation_level_high_multiplier", 1.3),
}


async def _feature_float(feature: FeatureType, chat_tid: int | None, default: float) -> float:
    value = await get_value(feature, chat_tid=chat_tid)
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def resolve_thresholds(provider: ModerationProvider, chat_tid: int | None) -> dict[str, float]:
    """Return the score each of the provider's native categories must reach to be flagged."""
    return {
        native.key: min(
            max(await _feature_float(native.flag, chat_tid, native.default_threshold), _MIN_THRESHOLD),
            _MAX_THRESHOLD,
        )
        for native in provider.native_categories
    }


async def resolve_level_multipliers(chat_tid: int | None) -> dict[DetectionLevel, float]:
    """Return the factor each detection level applies to a raw provider score."""
    return {
        level: await _feature_float(flag, chat_tid, default)
        for level, (flag, default) in _LEVEL_MULTIPLIER_FLAGS.items()
    }


def get_category_level(settings: AIModeratorModel | None, category: str) -> DetectionLevel:
    if settings is None:
        return DetectionLevel.NORMAL
    return getattr(settings, category, DetectionLevel.NORMAL)
