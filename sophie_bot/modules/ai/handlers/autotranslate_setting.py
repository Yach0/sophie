from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from stfu_tg import Template

from sophie_bot.constants import AI_EMOJI
from sophie_bot.db.models.ai.ai_autotranslate import AIAutotranslateModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.status_handler import StatusBoolHandlerABC
from sophie_bot.shared.lock_constants import SUPPORTED_LANGUAGES
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class AutoTranslateLanguageFSM(StatesGroup):
    waiting_for_code = State()


def _language_keyboard(excluded: set[str], recent: set[str]) -> InlineKeyboardMarkup:
    visible_codes = sorted(recent | excluded)
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if code in excluded else "") + SUPPORTED_LANGUAGES[code],
            callback_data=AutoTranslateLanguageCallback(code=code).pack(),
        )
        for code in visible_codes
        if code in SUPPORTED_LANGUAGES
    ]
    buttons.append(InlineKeyboardButton(text=_("Enter language code"), callback_data=AutoTranslateRawCallback().pack()))
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@flags.help(alias_to_modules=["language"], description=l_("Controls AI Auto translator"))
class AIAutotrans(StatusBoolHandlerABC):
    header_text = l_(lambda: Template(_("{ai_emoji} AI Auto translate"), ai_emoji=AI_EMOJI).to_html())
    change_command = "aiautotranslate"

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("aiautotranslate", "autotranslate")), UserRestricting(admin=True)

    async def get_status(self) -> bool:
        if not self.connection.db_model:
            return False
        return await AIAutotranslateModel.get_state(self.connection.db_model.iid)

    async def set_status(self, new_status: bool):
        await AIAutotranslateModel.set_state(self.connection.db_model, new_status)

    async def display_current_status(self):
        await super().display_current_status()
        await self.event.reply(
            _("Languages excluded from auto-translation:"),
            reply_markup=await _language_markup(self.connection.db_model.iid),
        )


async def _language_markup(chat_id):
    return _language_keyboard(
        await AIAutotranslateModel.get_excluded_languages(chat_id),
        await AIAutotranslateModel.get_recent_languages(chat_id),
    )


class AutoTranslateLanguageCallback(CallbackData, prefix="ai_at_lang"):
    code: str


class AutoTranslateRawCallback(CallbackData, prefix="ai_at_raw"):
    action: str = "enter"


class AutoTranslateLanguageHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AutoTranslateLanguageCallback.filter(), UserRestricting(admin=True)

    async def handle(self) -> None:
        code = self.callback_data.code
        if code not in SUPPORTED_LANGUAGES:
            await self.event.answer(_("Language not found."), show_alert=True)
            return
        await AIAutotranslateModel.toggle_excluded_language(self.connection.db_model, code)
        await self.event.answer()
        await self.edit_text(
            _("Languages excluded from auto-translation:"),
            reply_markup=await _language_markup(self.connection.db_model.iid),
        )


class AutoTranslateRawHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AutoTranslateRawCallback.filter(), UserRestricting(admin=True)

    async def handle(self) -> None:
        await self.data["state"].set_state(AutoTranslateLanguageFSM.waiting_for_code)
        await self.event.answer()
        if self.event.message:
            await self.event.message.answer(_("Send an ISO 639-1 language code."))


class AutoTranslateRawCodeHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return AutoTranslateLanguageFSM.waiting_for_code, UserRestricting(admin=True), F.text

    async def handle(self) -> None:
        code = (self.event.text or "").strip().lower()
        if code not in SUPPORTED_LANGUAGES:
            await self.event.reply(_("Invalid language code. Send an ISO 639-1 code."))
            return
        await AIAutotranslateModel.toggle_excluded_language(self.connection.db_model, code)
        await self.data["state"].clear()
        await self.event.reply(
            _("Languages excluded from auto-translation:"),
            reply_markup=await _language_markup(self.connection.db_model.iid),
        )
