from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.moderation.categories import ModerationCategory
from sophie_bot.utils.feature_flags import FeatureType


@dataclass(frozen=True)
class NativeCategory:
    """One category as the provider itself reports it.

    Thresholds live at this level rather than on the normalised category, because grouped
    categories score on different distributions: `sexual/minors` needs a far lower cut-off than
    `sexual`, yet both surface to users as ``ModerationCategory.SEXUAL``.
    """

    key: str
    flag: FeatureType
    default_threshold: float
    category: ModerationCategory


class ModerationProvider(Protocol):
    name: str
    native_categories: tuple[NativeCategory, ...]

    async def classify(self, history: AIMessageHistory) -> dict[str, float]:
        """Return the provider's raw per-native-category scores."""
        ...
