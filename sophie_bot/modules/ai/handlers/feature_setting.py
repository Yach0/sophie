from __future__ import annotations

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Template

from sophie_bot.constants import AI_EMOJI
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.status_handler import StatusBoolHandlerABC
from sophie_bot.utils import flags
from sophie_bot.utils.feature_flags import FeatureType, is_enabled, set_chat_override
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class AIFeatureSetting(StatusBoolHandlerABC):
    feature: FeatureType

    async def get_status(self) -> bool:
        return await is_enabled(self.feature, chat_tid=self.connection.tid)

    async def set_status(self, new_status: bool) -> None:
        await set_chat_override(self.feature, self.connection.tid, new_status)


@flags.help(description=l_("Controls AI chat summaries"))
class AIChatSummariesSetting(AIFeatureSetting):
    header_text = l_(lambda: Template(_("{ai_emoji} AI Chat Summaries"), ai_emoji=AI_EMOJI).to_html())
    change_command = "ai_summaries"
    feature = "ai_chat_summaries"

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("ai_summaries"), UserRestricting(admin=True)


@flags.help(description=l_("Controls AI note title generation"))
class AINoteTitlesSetting(AIFeatureSetting):
    header_text = l_(lambda: Template(_("{ai_emoji} AI Note Titles"), ai_emoji=AI_EMOJI).to_html())
    change_command = "ai_note_titles"
    feature = "ai_note_titles"

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("ai_note_titles"), UserRestricting(admin=True)
