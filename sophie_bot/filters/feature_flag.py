from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.utils.feature_flags import FeatureType, is_enabled


class FeatureFlagFilter(BaseFilter):
    """Filter that checks if a feature flag is enabled."""

    def __init__(self, feature: FeatureType, enabled: bool = True) -> None:
        self.feature = feature
        self.enabled = enabled

    async def __call__(self, event: Message | CallbackQuery, connection: ChatConnection | None = None) -> bool:
        """Check if the feature flag condition is met, using the connected chat when in a PM connection."""
        message = event.message if isinstance(event, CallbackQuery) else event
        if message is None:
            return False

        if connection is not None:
            chat_tid = connection.tid
        else:
            chat_tid = message.chat.id if isinstance(message, Message) else None

        flag_enabled = await is_enabled(self.feature, chat_tid=chat_tid)
        return flag_enabled == self.enabled
