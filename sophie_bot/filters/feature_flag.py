from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import FeatureType, is_enabled
from sophie_bot.utils.i18n import gettext as _


class FeatureFlagFilter(BaseFilter):
    """Filter that checks if a feature flag is enabled."""

    def __init__(self, feature: FeatureType, enabled: bool = True, *, notify_callback: bool = False) -> None:
        self.feature = feature
        self.enabled = enabled
        self.notify_callback = notify_callback

    async def __call__(
        self,
        event: Message | CallbackQuery,
        services: ApplicationServices,
        context: RequestContext,
    ) -> bool:
        """Check if the feature flag condition is met, using the connected chat when in a PM connection."""
        message = event.message if isinstance(event, CallbackQuery) else event
        if message is None:
            return False

        connection = context.connection
        chat_tid = (
            connection.tid if connection is not None else message.chat.id if isinstance(message, Message) else None
        )

        flag_enabled = await is_enabled(self.feature, chat_tid=chat_tid, redis=services.redis)
        matches = flag_enabled == self.enabled
        if not matches and self.notify_callback and isinstance(event, CallbackQuery):
            await event.answer(_("This feature is currently disabled."), show_alert=True)
        return matches
