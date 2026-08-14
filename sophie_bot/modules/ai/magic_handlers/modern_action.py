from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, Message
from pydantic import BaseModel
from pydantic_ai import Agent
from stfu_tg import Italic, Section, Template, Title
from stfu_tg.doc import Doc, Element, PreformattedHTML

from sophie_bot.constants import AI_EMOJI
from sophie_bot.db.models import ChatModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_default_model_plan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions, run_ai_text
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.modules.ai.utils.message_history import CHATBOT_CACHE_MESSAGE_LIMIT, AIMessageHistory
from sophie_bot.modules.filters.types.modern_action_abc import (
    ActionSetupMessage,
    ActionSetupTryAgainException,
    ModernActionABC,
    ModernActionSetting,
)
from sophie_bot.utils.ai_features import AI_FEATURE_FILTER
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class AIReplyActionDataModel(BaseModel):
    prompt: str


async def set_reply_text(event: Message | CallbackQuery, data: dict[str, Any]) -> AIReplyActionDataModel:
    if isinstance(event, CallbackQuery):
        raise TypeError("This handlers setup_confirm can only be used with messages")

    prompt = event.text

    if not prompt:
        raise ActionSetupTryAgainException(_("Please enter AI prompt"))

    return AIReplyActionDataModel(prompt=prompt)


async def reply_action_setup_message(_event: Message | CallbackQuery, _data: dict[str, Any]) -> ActionSetupMessage:
    text = Doc(
        _("Please send me the AI instruction to proceed!"),
        _("The AI will try to remember the chat context and will respond accordingly!"),
        _("For example, you can combine it with the warn filter: 'Tell the user how bad it is to speak profanity'"),
    ).to_html()

    return ActionSetupMessage(text=text)


class AIReplyAction(ModernActionABC[AIReplyActionDataModel]):
    name = "ai_text"

    icon = AI_EMOJI
    title = l_("AI Response")
    allow_warns = True

    interactive_setup = ModernActionSetting(
        title=l_("Reply to message"), setup_message=reply_action_setup_message, setup_confirm=set_reply_text
    )
    data_object = AIReplyActionDataModel

    @staticmethod
    def description(data: AIReplyActionDataModel) -> Element | str:
        return Section(Italic(data.prompt), title=_("Send an AI Respond with prompt"), title_underline=False)

    def settings(self, data: AIReplyActionDataModel) -> dict[str, ModernActionSetting]:
        return {
            "reply_text": ModernActionSetting(
                title=l_("Change AI prompt"),
                icon=AI_EMOJI,
                setup_message=reply_action_setup_message,
                setup_confirm=set_reply_text,
            ),
        }

    async def handle(self, message: Message, data: dict, filter_data: AIReplyActionDataModel) -> Element | None:
        connection: ChatConnection = data["connection"]

        if not (chat_db := await ChatModel.get_by_tid(connection.tid)):
            raise SophieException("Chat not found in database")

        if not (
            (message.text or message.caption) and await AIQuotaFilter(AI_FEATURE_FILTER).__call__(message, chat_db)
        ):
            return

        messages = AIMessageHistory()
        messages.add_system(filter_data.prompt)
        await messages.add_from_cache(message.chat.id, limit=CHATBOT_CACHE_MESSAGE_LIMIT, fold_background=True)
        await messages.add_from_message(message)
        messages.apply_context_block()
        model_plan = await get_chat_default_model_plan(connection.db_model.iid, chat_tid=connection.db_model.tid)

        result = await run_ai_text(
            Agent(model_plan.primary, output_type=str),
            user_prompt=messages.prompt,
            message_history=messages.message_history,
            request_options=AIRequestOptions(user_tracking_id=chat_db.iid),
            model_plan=model_plan,
        )

        if result.usage and result.usage.total_tokens:
            await charge_ai_usage(
                chat_db.iid, AI_FEATURE_FILTER, result.served_model or model_plan.primary, result.usage
            )

        return Doc(
            Title(Template(_("{ai_emoji} AI Response"), ai_emoji=AI_EMOJI)),
            PreformattedHTML(ai_markdown_to_html(str(result.output))),
        )
