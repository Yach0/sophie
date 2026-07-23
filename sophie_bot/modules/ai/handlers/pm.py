from typing import Any

from aiogram import F, Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from stfu_tg import Bold, Doc, Template, Url

from sophie_bot.config import CONFIG
from sophie_bot.constants import AI_EMOJI
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.callbacks import AIChatCallback, AIHelpStartUrlCallback
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.fsm.pm import (
    AI_PM_HELP_MODE,
    AI_PM_NORMAL_MODE,
    AI_PM_RESET,
    AI_PM_STOP_HELP_TEXT,
    AI_PM_STOP_TEXT,
    AiPMFSM,
)
from sophie_bot.modules.ai.utils.ai_chatbot_reply import ai_chatbot_reply
from sophie_bot.modules.ai.utils.ai_help_mode import is_help_mode, set_help_mode
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.handlers import SophieMessageCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def _build_keyboard(help_mode: bool) -> ReplyKeyboardMarkup:
    exit_text = AI_PM_STOP_HELP_TEXT if help_mode else AI_PM_STOP_TEXT
    switch_text = AI_PM_NORMAL_MODE if help_mode else AI_PM_HELP_MODE
    rows = [
        [KeyboardButton(text=str(exit_text)), KeyboardButton(text=str(AI_PM_RESET))],
        [KeyboardButton(text=str(switch_text))],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


@flags.help(description=l_("Start the AI ChatBot mode"))
class AiPmInitialize(SophieMessageCallbackQueryHandler):
    @classmethod
    def register(cls, router: Router):
        router.message.register(cls, CMDFilter("ai"), ChatTypeFilter("private"))
        router.message.register(cls, AIHelpStartUrlCallback.filter(), ChatTypeFilter("private"))
        router.callback_query.register(cls, AIChatCallback.filter(), ChatTypeFilter("private"))

    async def handle(self) -> Any:
        # Reaching the AI through the /help button asks about Sophie itself, so that entry point
        # gets the Sophie-help assistant; /ai always means the general one.
        # Both the /help button and the deep link from a group mean "help me with Sophie".
        help_mode = self.data.get("callback_data") is not None or self.data.get("command_start") is not None

        doc = Doc(
            Bold(
                Template(
                    _("{ai_emoji} Entered the Sophie help mode, ask me anything about using Sophie.")
                    if help_mode
                    else _("{ai_emoji} Entered to the AI Mode, in this mode you can directly interact with the AI."),
                    ai_emoji=AI_EMOJI,
                )
            ),
            Template(
                _("By using the AI, you agree to the {privacy_policy} of the bot and third party AI services used."),
                privacy_policy=Url(_("privacy policy"), CONFIG.privacy_link),
            ),
            _("Click on the button below to exit."),
        )

        state = self.data["state"]
        await state.set_state(AiPMFSM.in_ai)
        await set_help_mode(state, help_mode)

        await self.answer(str(doc), disable_web_page_preview=True)

        initial_fake_ai_response = (
            _("Hello! What would you like to know about Sophie?") if help_mode else _("Hello! How can I help you?")
        )
        await self.message.answer(initial_fake_ai_response, reply_markup=_build_keyboard(help_mode))


class AiPmNormalMode(SophieMessageHandler):
    """Leave the Sophie-help assistant without leaving the AI mode."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return F.text == AI_PM_NORMAL_MODE, ChatTypeFilter("private")

    async def handle(self) -> Any:
        await set_help_mode(self.data["state"], False)
        await self.event.reply(_("Switched to the normal AI mode."), reply_markup=_build_keyboard(help_mode=False))


class AiPmHelpMode(SophieMessageHandler):
    """Enter the Sophie-help assistant from an AI session already in progress."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return F.text == AI_PM_HELP_MODE, ChatTypeFilter("private")

    async def handle(self) -> Any:
        await set_help_mode(self.data["state"], True)
        await self.event.reply(
            _("Switched to the Sophie help mode, ask me anything about using Sophie."),
            reply_markup=_build_keyboard(help_mode=True),
        )


class AiPmStop(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        # Sophie-help labels the same button differently, so both spellings must leave the AI mode.
        return F.text.in_([AI_PM_STOP_TEXT, AI_PM_STOP_HELP_TEXT]), ChatTypeFilter("private")

    async def handle(self) -> Any:
        # Clearing the state drops the Sophie-help flag stored alongside it.
        await self.data["state"].clear()
        await self.event.reply(_("The AI mode has been exited."), reply_markup=ReplyKeyboardRemove())


@flags.status("typing")
@flags.ai_cache(cache_handler_result=True)
class AiPmHandle(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AiPMFSM.in_ai, ChatTypeFilter("private"), AIQuotaFilter(AI_FEATURE_CHATBOT)

    async def handle(self) -> Any:
        keyboard = _build_keyboard(await is_help_mode(self.data["state"]))

        self.data["ai_msg_cache"] = True
        return await ai_chatbot_reply(self.event, self.connection, mode=self.data["ai_mode"], reply_markup=keyboard)
