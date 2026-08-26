from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from ass_tg.types import OptionalArg, TextArg
from ass_tg.types.base_abc import ArgFabric
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


def _language_keyboard(excluded: set[str], recent: set[str], code: str | None = None) -> InlineKeyboardMarkup:
    visible_codes = [code] if code else sorted(recent | excluded)
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if code in excluded else "") + SUPPORTED_LANGUAGES[code],
            callback_data=AutoTranslateLanguageCallback(code=code).pack(),
        )
        for code in visible_codes
        if code in SUPPORTED_LANGUAGES
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@flags.help(alias_to_modules=["language"], description=l_("Controls AI Auto translator"))
class AIAutotrans(StatusBoolHandlerABC):
    header_text = l_(lambda: Template(_("{ai_emoji} AI Auto translate"), ai_emoji=AI_EMOJI).to_html())
    change_command = "aiautotranslate"

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("aiautotranslate", "autotranslate")), UserRestricting(admin=True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        arguments = await super().handler_args(message, data)
        arguments["language_code"] = OptionalArg(TextArg(l_("?Language code")))
        return arguments

    async def get_status(self) -> bool:
        if not self.connection.db_model:
            return False
        return await AIAutotranslateModel.get_state(self.connection.db_model.iid)

    async def set_status(self, new_status: bool):
        await AIAutotranslateModel.set_state(self.connection.db_model, new_status)

    async def display_current_status(self):
        code = self.data.get("language_code")
        if code is not None:
            code = code.strip().lower()
            if code not in SUPPORTED_LANGUAGES:
                await self.event.reply(_("Invalid language code. Use a supported language code."))
                return
        await super().display_current_status()
        await self.event.reply(
            _("Languages excluded from auto-translation:"),
            reply_markup=await _language_markup(self.connection.db_model.iid, code),
        )


async def _language_markup(chat_id, code: str | None = None):
    if code is not None:
        code = code.strip().lower()
        if code not in SUPPORTED_LANGUAGES:
            return None
    return _language_keyboard(
        await AIAutotranslateModel.get_excluded_languages(chat_id),
        await AIAutotranslateModel.get_recent_languages(chat_id),
        code,
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
        await self.event.answer()
        await self.edit_text(
            _("Languages excluded from auto-translation:"),
            reply_markup=await _language_markup(self.connection.db_model.iid),
        )
