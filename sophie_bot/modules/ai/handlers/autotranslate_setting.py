from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from stfu_tg import Template

from sophie_bot.constants import AI_EMOJI
from sophie_bot.db.models.ai.ai_autotranslate import AIAutotranslateModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.status_handler import StatusBoolHandlerABC
from sophie_bot.shared.lock_constants import SUPPORTED_LANGUAGES
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def _language_keyboard(excluded: set[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if code in excluded else "") + name,
            callback_data=AutoTranslateLanguageCallback(code=code).pack(),
        )
        for code, name in sorted(SUPPORTED_LANGUAGES.items())
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[index : index + 2] for index in range(0, len(buttons), 2)])


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

        db_model = await AIAutotranslateModel.get_state(self.connection.db_model.iid)
        return bool(db_model)

    async def set_status(self, new_status: bool):
        await AIAutotranslateModel.set_state(self.connection.db_model, new_status)

    async def display_current_status(self):
        await super().display_current_status()
        excluded = await AIAutotranslateModel.get_excluded_languages(self.connection.db_model.iid)
        await self.event.reply(
            _("Languages excluded from auto-translation:"),
            reply_markup=_language_keyboard(excluded),
        )


class AutoTranslateLanguageCallback(CallbackData, prefix="ai_at_lang"):
    code: str


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
        excluded = await AIAutotranslateModel.get_excluded_languages(self.connection.db_model.iid)
        await self.event.answer()
        await self.edit_text(_("Languages excluded from auto-translation:"), reply_markup=_language_keyboard(excluded))
