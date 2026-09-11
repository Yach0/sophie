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
from sophie_bot.modules.utils_.action_config_wizard import (
    ActionSetupTryAgainException,
    ActionWizardSetting,
    ActionWizardSpec,
)
from sophie_bot.shared.actions import ActionDefinition, ModernActionABC
from sophie_bot.utils.ai_features import AI_FEATURE_FILTER
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class AIReplyActionDataModel(BaseModel):
    prompt: str


async def set_reply_text(event: Message | CallbackQuery, _data: dict[str, Any]) -> AIReplyActionDataModel:
    if isinstance(event, CallbackQuery):
        raise TypeError("This handlers setup_confirm can only be used with messages")

    prompt = event.text

    if not prompt:
        raise ActionSetupTryAgainException(_("Please enter AI prompt"))

    return AIReplyActionDataModel(prompt=prompt)


async def reply_action_setup_message(_event: Message | CallbackQuery, _data: dict[str, Any]) -> Element:
    return Doc(
        _("Please send me the AI instruction to proceed!"),
        _("The AI will try to remember the chat context and will respond accordingly!"),
        _("For example, you can combine it with the warn filter: 'Tell the user how bad it is to speak profanity'"),
    )


def build_action_wizard_specs() -> dict[str, ActionWizardSpec]:
    setting = ActionWizardSetting(
        title=l_("Reply to message"),
        setup_message=reply_action_setup_message,
        setup_confirm=set_reply_text,
    )
    return {
        AI_REPLY_ACTION.name: ActionWizardSpec(
            interactive_setup=setting,
            settings=lambda _data: {
                "reply_text": ActionWizardSetting(
                    title=l_("Change AI prompt"),
                    icon=AI_EMOJI,
                    setup_message=reply_action_setup_message,
                    setup_confirm=set_reply_text,
                )
            },
        )
    }


AI_REPLY_ACTION = ActionDefinition[AIReplyActionDataModel](
    name="ai_text",
    icon=AI_EMOJI,
    title=l_("AI Response"),
    data_object=AIReplyActionDataModel,
    allow_warns=True,
    has_interactive_setup=True,
)


class AIReplyAction(ModernActionABC[AIReplyActionDataModel]):
    definition = AI_REPLY_ACTION

    @staticmethod
    def description(data: AIReplyActionDataModel) -> Element | str:
        return Section(Italic(data.prompt), title=_("Send an AI Respond with prompt"), title_underline=False)

    async def handle(self, message: Message, data: dict, filter_data: AIReplyActionDataModel) -> Element | None:
        connection: ChatConnection = data["context"].connection

        if not (chat_db := await ChatModel.get_by_tid(connection.tid)):
            raise SophieException("Chat not found in database")

        if not (
            (message.text or message.caption)
            and await AIQuotaFilter(AI_FEATURE_FILTER).__call__(
                message,
                data["context"],
                data["services"],
            )
        ):
            return None

        messages = AIMessageHistory(services=data["services"])
        messages.add_system(filter_data.prompt)
        await messages.add_from_cache(message.chat.id, limit=CHATBOT_CACHE_MESSAGE_LIMIT, fold_background=True)
        await messages.add_from_message(message)
        messages.apply_context_block()
        model_plan = await get_chat_default_model_plan(
            connection.db_model.iid,
            chat_tid=connection.db_model.tid,
            redis=data["services"].redis,
        )

        result = await run_ai_text(
            Agent(model_plan.primary, output_type=str),
            user_prompt=messages.prompt,
            message_history=messages.message_history,
            request_options=AIRequestOptions(user_tracking_id=chat_db.iid),
            model_plan=model_plan,
        )

        if result.usage and result.usage.total_tokens:
            await charge_ai_usage(
                chat_db.iid,
                AI_FEATURE_FILTER,
                result.served_model or model_plan.primary,
                result.usage,
                redis=data["services"].redis,
            )

        return Doc(
            Title(Template(_("{ai_emoji} AI Response"), ai_emoji=AI_EMOJI)),
            PreformattedHTML(ai_markdown_to_html(str(result.output))),
        )
