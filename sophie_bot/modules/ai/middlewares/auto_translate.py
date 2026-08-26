from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from sophie_bot.db.models import AIAutotranslateModel, ChatModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.ai.handlers.translate import AiTranslate
from sophie_bot.modules.ai.utils.ai_quota import check_quota
from sophie_bot.modules.ai.utils.detect_lang import should_auto_translate_text
from sophie_bot.shared.lang_detect import lang_code_to_language
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import I18nNew
from sophie_bot.utils.logger import log


class AiAutoTranslateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_db: ChatModel | None = data.get("chat_db", None)
        i18n: I18nNew = data["i18n"]

        result = await handler(event, data)

        if (
            chat_db
            and chat_db.type != ChatType.private
            and await is_enabled("ai_translations", chat_tid=chat_db.tid)
            and (capabilities := data.get("ai_capabilities"))
            and capabilities.ai_enabled
            and isinstance(event, Message)
            and await AIAutotranslateModel.get_state(chat_db.iid)
        ):
            quota_check = await check_quota(chat_db.iid)
            if not quota_check.allowed:
                return result

            data["autotranslate"] = True
            data["text"] = event.text or event.caption or ""
            data["voice"] = event.voice

            if not data["voice"]:
                if not data["text"]:
                    return result
                if data["text"].startswith("http") or data["text"].startswith("/"):
                    log.debug("AiAutoTranslateMiddleware: Ignoring non-text message")
                    return result
                if len(data["text"]) <= 5:
                    log.debug("AiAutoTranslateMiddleware: Ignoring short message")
                    return result

            text_to_detect = data["text"].lower()

            data["silent_error"] = True

            if data.get("voice"):
                log.debug("AiAutoTranslateMiddleware: Voice message - Translating anyway!")
                await AiTranslate(event, **data)

            excluded_languages = {
                lang_code_to_language(language_code)
                for language_code in await AIAutotranslateModel.get_excluded_languages(chat_db.iid)
            }
            if should_auto_translate_text(
                text_to_detect, lang_code_to_language(i18n.current_locale_iso_639_1), excluded_languages
            ):
                log.debug("AiAutoTranslateMiddleware: Detected another language, translating!")
                await AiTranslate(event, **data)

        return result
