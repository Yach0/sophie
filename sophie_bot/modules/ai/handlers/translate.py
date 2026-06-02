from typing import Any

from aiogram import Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from stfu_tg import (
    BlockQuote,
    Bold,
    Doc,
    HList,
    PreformattedHTML,
    Section,
    Template,
    Title,
)

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.filters.ai_enabled import AIEnabledFilter
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT
from sophie_bot.modules.ai.json_schemas.translate import AITranslateResponseSchema
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed, ai_request_failed_message
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_translations_model
from sophie_bot.modules.ai.utils.ai_header import ai_credit_header
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.modules.ai.utils.ai_tasks import AIStructuredTask, run_structured_task
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.transform_audio import transform_voice_to_text
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_AUTO_TRANSLATE, AI_FEATURE_TRANSLATE
from sophie_bot.utils.feature_flags import get_value
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log


async def _resolve_translation_input(event: Message, data: dict) -> tuple[str, bool]:
    """Determine what text to translate and whether it's voice."""
    is_autotranslate: bool = data.get("autotranslate", False)

    is_voice = False
    if event.reply_to_message and event.reply_to_message.voice and not is_autotranslate:
        to_translate = await transform_voice_to_text(event.reply_to_message.voice)
        is_voice = True
    elif event.reply_to_message and not is_autotranslate:
        sticker_emoji = event.reply_to_message.sticker.emoji if event.reply_to_message.sticker else ""
        to_translate = event.reply_to_message.text or event.reply_to_message.caption or sticker_emoji or ""
    elif data.get("voice"):
        to_translate = ""
        is_voice = True
    else:
        to_translate = data.get("text", "")

    return to_translate, is_voice


def _build_translate_reply_doc(
    translated,
    language_name: str,
    is_autotranslate: bool,
    is_voice: bool,
    quota_header,
) -> Doc:
    """Format the translation response document."""
    return Doc(
        HList(
            Title(AI_GENERATED_TEXT),
            _("Auto Translator") if is_autotranslate else _("Translator"),
            f"({_('Voice')})" if is_voice else None,
            quota_header,
        ),
        (
            Bold(
                Template(
                    _("From {from_lang} to {to_lang}"),
                    from_lang=f"{translated.origin_language_emoji} {translated.origin_language_name}",
                    to_lang=language_name,
                )
            )
            if not is_voice
            else None
        ),
        BlockQuote(PreformattedHTML(ai_markdown_to_html(translated.translated_text)), expandable=True),
        (
            Section(translated.translation_explanations, title=_("Translation Notes"))
            if translated.translation_explanations
            else None
        ),
    )


async def text_or_reply(message: Message | None, _data: dict):
    if message and message.reply_to_message:
        return {}
    return {
        "text": TextArg(l_("Text to translate")),
    }


@flags.help(
    alias_to_modules=["language"],
    description=l_(
        "Translates the given (or replied) text to the chat's selected language. Also transcribes the "
        "replied voice message to text"
    ),
)
@flags.status("typing")
@flags.disableable(name="translate")
@flags.ai_cache(cache_handler_result=True)
class AiTranslate(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("aitranslate", "translate", "tr")), AIEnabledFilter(), AIQuotaFilter(AI_FEATURE_TRANSLATE)

    @classmethod
    def register(cls, router: Router) -> None:
        router.message.register(cls, *cls.filters(), flags={"args": text_or_reply})

    async def handle(self) -> Any:
        is_autotranslate: bool = self.data.get("autotranslate", False)

        language_name = self.data["i18n"].current_locale_display

        to_translate, is_voice = await _resolve_translation_input(self.event, self.data)

        reply_to_message = self.event.reply_to_message
        reply_has_translatable_media = bool(
            reply_to_message
            and (
                reply_to_message.photo
                or reply_to_message.sticker
                or reply_to_message.animation
                or reply_to_message.video
            )
        )

        if not to_translate.strip() and not reply_has_translatable_media:
            if self.data.get("silent_error"):
                return
            await self.event.reply(_("Please provide text to translate."))
            return

        # AI Context
        ai_context = AIMessageHistory()
        if reply_to_message and (reply_to_message.photo or reply_to_message.sticker or reply_to_message.animation):
            ai_context.add_system(
                Template(
                    _("If applicable, translate the photo to {language_name}"), language_name=language_name
                ).to_html()
            )
            await ai_context.add_from_message(reply_to_message, disable_name=True)

        translator_prompt = str(await get_value("ai_translate_system_prompt", chat_tid=self.event.chat.id))
        ai_context.add_system(
            "\n".join(
                (
                    translator_prompt,
                    Template(
                        _("Translate the following text to {language_name}:\n{to_translate}"),
                        language_name=language_name,
                        to_translate=to_translate,
                    ).to_html(),
                )
            )
        )

        log.debug("AiTranslate", ai_context=ai_context.history_debug())

        model = await get_chat_translations_model(self.connection.db_model.iid, chat_tid=self.connection.db_model.tid)

        try:
            result = await run_structured_task(
                AIStructuredTask(
                    instructions="",
                    output_type=AITranslateResponseSchema,
                    feature=AI_FEATURE_AUTO_TRANSLATE if is_autotranslate else AI_FEATURE_TRANSLATE,
                    service_tier_feature_key="ai_translations_service_tier",
                ),
                model,
                ai_context,
                chat_iid=self.connection.db_model.iid,
                chat_tid=self.event.chat.id,
            )
            translated = result.output
        except AIRequestFailed as err:
            if self.data.get("silent_error"):
                return
            await self.event.reply(
                **ai_request_failed_message(err.sentry_event_id, title=_("Error generating translation"))
            )
            return

        # Prevent extra translating
        if is_autotranslate and not is_voice and not translated.needs_translation:
            log.debug("AiTranslate: AI do not think it needs translation, skipping.")
            return
        if is_autotranslate and to_translate.lower().strip() == translated.translated_text.lower().strip():
            log.debug("AiTranslate: AI gave the exact same text, skipping.")
            return

        quota_info = await get_quota_info(self.connection.db_model.iid)
        quota_header = None
        if quota_info and quota_info.total_credits > 0:
            quota_percentage = int((quota_info.remaining_credits / quota_info.total_credits) * 100)
            quota_header = ai_credit_header(quota_percentage)

        doc = _build_translate_reply_doc(translated, language_name, is_autotranslate, is_voice, quota_header)

        await self.event.reply(str(doc))
