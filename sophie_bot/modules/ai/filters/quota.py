from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Union

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Filter
from aiogram.types import Message

from sophie_bot.db.models.ai.ai_quota import AIQuotaModel
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.metrics.ai import track_ai_quota_exceeded
from sophie_bot.modules.ai.utils.ai_quota import check_quota, get_period_end, get_quota_info, _current_period_start
from sophie_bot.modules.ai.utils.ai_quota_docs import (
    build_chatbot_quota_exhausted_doc,
    build_feature_quota_exhausted_doc,
)
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT, AIFeature


class AIQuotaFilter(Filter):
    def __init__(self, feature: AIFeature):
        self.feature = feature
        self._is_chatbot = feature == AI_FEATURE_CHATBOT

    async def __call__(self, message: Message, chat_db: ChatModel) -> Union[bool, Dict[str, Any]]:
        if not chat_db:
            raise SkipHandler

        result = await check_quota(chat_db.iid)
        if result.allowed:
            quota_info = await get_quota_info(chat_db.iid)
            return {"quota_info": quota_info}

        track_ai_quota_exceeded(feature=str(self.feature), chat_type=message.chat.type)
        if self._is_chatbot:
            quota_info = await get_quota_info(chat_db.iid)
            if quota_info:
                period_end = quota_info.period_end
            else:
                period_end = get_period_end(_current_period_start())
            await message.reply(
                str(build_chatbot_quota_exhausted_doc(quota_info.total_credits if quota_info else "?", period_end))
            )
        else:
            quota_info = await get_quota_info(chat_db.iid)
            if quota_info:
                period_end = quota_info.period_end
                period_start = quota_info.period_start
            else:
                period_start = _current_period_start()
                period_end = get_period_end(period_start)

            quota_model = await AIQuotaModel.get_for_chat(chat_db.iid)
            already_notified = bool(
                quota_model
                and quota_model.exhausted_notified_period_start == period_start
                and quota_model.exhausted_notified_at is not None
            )
            if not already_notified:
                if quota_model:
                    quota_model.exhausted_notified_period_start = period_start
                    quota_model.exhausted_notified_at = datetime.now(timezone.utc)
                    await quota_model.save()

                await message.reply(str(build_feature_quota_exhausted_doc(period_end)))

        raise SkipHandler
