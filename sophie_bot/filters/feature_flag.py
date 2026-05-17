from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from sophie_bot.utils.feature_flags import FeatureType, is_enabled


class FeatureFlagFilter(BaseFilter):
    """Filter that checks if a feature flag is enabled."""

    def __init__(self, feature: FeatureType, enabled: bool = True) -> None:
        """
        Initialize the feature flag filter.

        Args:
            feature: The feature flag to check (must be a valid FeatureType)
            enabled: If True, handler is enabled when flag is True
                    If False, handler is enabled when flag is False (reverse logic)
        """
        self.feature = feature
        self.enabled = enabled

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        """Check if the feature flag condition is met."""
        message = event.message if isinstance(event, CallbackQuery) else event
        chat_tid = message.chat.id if isinstance(message, Message) else None
        flag_enabled = await is_enabled(self.feature, chat_tid=chat_tid)
        return flag_enabled == self.enabled
